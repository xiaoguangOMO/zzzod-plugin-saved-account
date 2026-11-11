"""让本地安装（还没上架商店）的插件在「插件管理 → 详情」里也显示 README 说明。

一条龙的详情弹窗只从插件商店条目里取 ``readmeHtml``：本地插件没有商店条目，
弹窗的「插件说明」区域就只显示一句「该插件暂无可展示的说明」。这里在弹窗
构造前把插件目录的 README.md 渲染成 HTML 补进去；商店条目有说明时保持原样，
不覆盖商店内容，也不改动一条龙的源码文件。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from one_dragon.utils.log_utils import log

try:  # 仓库布局（const.py）与插件目录布局（xxx_const.py）都能用
    from .const import APP_NAME
except ImportError:  # pragma: no cover - 取决于插件放置方式
    from .zzz_saved_account_const import APP_NAME

from .local_readme import read_readme_html

_PACKAGE_DIR = Path(__file__).resolve().parent


def apply_ui_patch() -> bool:
    """给插件详情弹窗装一个「本地 README 兜底」，返回是否安装成功。

    没有 Qt 环境（例如纯命令行跑一条龙）时静默跳过，不影响补丁本身。
    """
    try:
        from one_dragon_qt.view.plugin.plugin_detail_dialog import PluginDetailDialog
    except Exception:  # noqa: BLE001 - 没装 Qt 时属于正常情况
        log.debug('未加载插件详情弹窗，跳过本地 README 注入')
        return False

    if getattr(PluginDetailDialog, '_saved_account_readme_patched', False):
        return True

    original_init = PluginDetailDialog.__init__

    def patched_init(self, data, parent=None):
        try:
            if not getattr(data, 'readme_html', '') \
                    and getattr(data, 'title', '') == APP_NAME:
                html = read_readme_html(_PACKAGE_DIR)
                if html:
                    data = dataclasses.replace(data, readme_html=html)
        except Exception:  # noqa: BLE001 - 说明只是展示，失败不能影响弹窗
            log.error('读取插件本地说明失败', exc_info=True)
        original_init(self, data, parent)

    PluginDetailDialog.__init__ = patched_init
    PluginDetailDialog._saved_account_readme_patched = True
    return True
