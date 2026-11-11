"""把插件目录里的 README.md 转成插件详情弹窗能用的 HTML。

一条龙的插件详情弹窗只在「插件商店条目」里拿 ``readmeHtml``，本地安装或还没上架的
插件没有商店条目，弹窗就只剩一句「该插件暂无可展示的说明」。这里自带一个极简的
Markdown 渲染器，只覆盖 README 里实际用到的语法（标题、段落、列表、表格、引用、
代码块、粗体、行内代码、链接），不引入任何第三方依赖。

渲染失败时返回空字符串，调用方会退回一条龙原生行为，不影响插件加载。
"""

from __future__ import annotations

import html
import re
from pathlib import Path

README_FILE_NAME = 'README.md'
USAGE_FILE_NAME = 'USAGE.md'

_HEADING_PATTERN = re.compile(r'^(#{1,6})\s+(.*)$')
_ORDERED_ITEM_PATTERN = re.compile(r'^\s*\d+[.)]\s+(.*)$')
_UNORDERED_ITEM_PATTERN = re.compile(r'^\s*[-*+]\s+(.*)$')
_TABLE_SEPARATOR_PATTERN = re.compile(r'^\s*\|?[\s:|-]+\|?\s*$')
_HORIZONTAL_RULE_PATTERN = re.compile(r'^\s*([-*_])\s*(?:\1\s*){2,}$')
_INLINE_CODE_PATTERN = re.compile(r'`([^`]+)`')
_BOLD_PATTERN = re.compile(r'\*\*([^*]+)\*\*')
_LINK_PATTERN = re.compile(r'\[([^\]]+)\]\(([^)\s]+)\)')


def _render_inline(text: str) -> str:
    """渲染行内语法，顺序为：转义 → 行内代码 → 粗体 → 链接。"""
    codes: list[str] = []

    def _stash_code(match: re.Match[str]) -> str:
        codes.append(f'<code>{html.escape(match.group(1))}</code>')
        return f'\x00{len(codes) - 1}\x00'

    stashed = _INLINE_CODE_PATTERN.sub(_stash_code, text)
    escaped = html.escape(stashed)
    escaped = _BOLD_PATTERN.sub(r'<b>\1</b>', escaped)

    def _render_link(match: re.Match[str]) -> str:
        label = match.group(1)
        url = match.group(2)
        if not url.lower().startswith(('http://', 'https://')):
            return label
        return f'<a href="{html.escape(url)}">{label}</a>'

    escaped = _LINK_PATTERN.sub(_render_link, escaped)
    for index, code_html in enumerate(codes):
        escaped = escaped.replace(f'\x00{index}\x00', code_html)
    return escaped


def _split_table_row(line: str) -> list[str]:
    """拆分表格行，去掉首尾竖线。"""
    stripped = line.strip()
    if stripped.startswith('|'):
        stripped = stripped[1:]
    if stripped.endswith('|'):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split('|')]


def markdown_to_html(markdown: str) -> str:
    """把 README 的 Markdown 子集渲染成 HTML；任何异常都向上抛给调用方兜底。"""
    lines = markdown.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    blocks: list[str] = []

    paragraph: list[str] = []
    list_tag = ''
    list_items: list[str] = []
    quote_lines: list[str] = []
    code_lines: list[str] = []
    in_code = False
    index = 0

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append('<p>' + _render_inline(' '.join(paragraph)) + '</p>')
            paragraph.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if list_tag and list_items:
            items = ''.join(f'<li>{_render_inline(item)}</li>' for item in list_items)
            blocks.append(f'<{list_tag}>{items}</{list_tag}>')
        list_items.clear()
        list_tag = ''

    def flush_quote() -> None:
        if quote_lines:
            blocks.append('<blockquote>' + _render_inline(' '.join(quote_lines)) + '</blockquote>')
            quote_lines.clear()

    def flush_code() -> None:
        if code_lines:
            blocks.append('<pre>' + html.escape('\n'.join(code_lines)) + '</pre>')
            code_lines.clear()

    def flush_all() -> None:
        flush_paragraph()
        flush_list()
        flush_quote()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if in_code:
            if stripped.startswith('```'):
                in_code = False
                flush_code()
            else:
                code_lines.append(line)
            index += 1
            continue

        if stripped.startswith('```'):
            flush_all()
            in_code = True
            index += 1
            continue

        if not stripped:
            flush_all()
            index += 1
            continue

        # 列表项和段落都可能跨行续写（常见于中文 README 的软换行），
        # 缩进且上一行非空时并回上一项，避免把一句话拆成列表 + 段落。
        is_continuation = index > 0 and bool(lines[index - 1].strip()) and line[:1].isspace()
        if is_continuation and list_items:
            list_items[-1] = list_items[-1] + ' ' + stripped
            index += 1
            continue
        if is_continuation and paragraph:
            paragraph.append(stripped)
            index += 1
            continue

        heading = _HEADING_PATTERN.match(stripped)
        if heading:
            flush_all()
            level = len(heading.group(1))
            blocks.append(f'<h{level}>{_render_inline(heading.group(2).strip())}</h{level}>')
            index += 1
            continue

        if _HORIZONTAL_RULE_PATTERN.match(stripped):
            flush_all()
            blocks.append('<hr/>')
            index += 1
            continue

        if stripped.startswith('>'):
            flush_list()
            flush_paragraph()
            quote_lines.append(stripped.lstrip('>').strip())
            index += 1
            continue
        flush_quote()

        if stripped.startswith('|') and index + 1 < len(lines) \
                and _TABLE_SEPARATOR_PATTERN.match(lines[index + 1]) \
                and '-' in lines[index + 1]:
            flush_all()
            header_cells = _split_table_row(stripped)
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and lines[index].strip().startswith('|'):
                rows.append(_split_table_row(lines[index]))
                index += 1
            head_html = ''.join(f'<th>{_render_inline(cell)}</th>' for cell in header_cells)
            body_html = ''.join(
                '<tr>' + ''.join(f'<td>{_render_inline(cell)}</td>' for cell in row) + '</tr>'
                for row in rows
            )
            blocks.append(f'<table border="1" cellspacing="0" cellpadding="4">'
                          f'<tr>{head_html}</tr>{body_html}</table>')
            continue

        unordered = _UNORDERED_ITEM_PATTERN.match(line)
        ordered = _ORDERED_ITEM_PATTERN.match(line)
        if unordered or ordered:
            flush_paragraph()
            flush_quote()
            wanted = 'ul' if unordered else 'ol'
            if list_tag and list_tag != wanted:
                flush_list()
            list_tag = wanted
            list_items.append((unordered or ordered).group(1).strip())
            index += 1
            continue

        flush_list()
        paragraph.append(stripped)
        index += 1

    if in_code:
        flush_code()
    flush_all()
    return '\n'.join(blocks)


def read_readme_html(package_dir: Path) -> str:
    """读取插件自带的说明文档，找不到时返回空字符串。

    优先用插件包内的 ``USAGE.md``（随 zip 发布，商店外的本地安装也有说明），
    其次插件包内的 README.md，最后仓库根的 README.md（开发时的目录布局）。
    """
    candidates = [
        package_dir / USAGE_FILE_NAME,
        package_dir / README_FILE_NAME,
        package_dir.parent / README_FILE_NAME,
    ]
    for path in candidates:
        try:
            if path.is_file():
                return markdown_to_html(path.read_text(encoding='utf-8-sig'))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
    return ''
