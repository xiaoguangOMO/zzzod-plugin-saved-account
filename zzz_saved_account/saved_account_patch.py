"""已保存账号免密切换补丁。

一条龙原生的多账号切换流程是「退出当前账号 -> 输入账号密码登录下一个账号」。
实际上游戏会在登录界面弹出 miHoYo 的「已保存的登录记录」列表，只要点一下
目标账号就能直接登录，不需要任何密码。

本模块在插件被加载时给 ``EnterGame`` 打补丁：

- ``check_login_related``：在原有登录判断之前，先尝试识别账号选择弹窗，
  命中时直接点选当前实例配置的目标账号；如果先落到「输入手机号/密码」页，
  会点左上角返回，回到已保存账号列表；
- ``handle_init``：重置本补丁自己的重试计数；
- ``GameAccountConfig.has_login_info``：配置了 ``saved_account`` 时视为已有登录方式，
  避免一条龙把「强制重新登录」降级掉。

目标账号不新增核心配置项，直接复用每个实例的 ``config/<实例>/game_account.yml``
里的自定义字段::

    saved_account: '138******00'

留空或不写该字段时，补丁完全不介入，行为和原生一致。
"""

from __future__ import annotations

import copy
import functools
import re
import time
from dataclasses import dataclass, field

from cv2.typing import MatLike

from one_dragon.base.config.game_account_config import GameAccountConfig
from one_dragon.base.geometry.point import Point
from one_dragon.base.geometry.rectangle import Rect
from one_dragon.base.matcher.ocr.ocr_match_result import OcrMatchResult
from one_dragon.base.operation.operation_round_result import OperationRoundResult
from one_dragon.utils import cv2_utils
from one_dragon.utils.log_utils import log
from zzz_od.operation.enter_game.enter_game import EnterGame
from zzz_od.operation.enter_game.switch_account import SwitchAccount

# 账号选择弹窗的识别区域（1920x1080 标准坐标系）
# 覆盖整个弹窗内容区，避开标题和右上角关闭按钮
DIALOG_RECT = Rect(600, 380, 1330, 900)

# 「输入手机号/密码」页左上角的返回按钮（1920x1080），点它回到已保存账号列表
# 这个页面是点「登录其他账号」后出现的，一条龙原生会在这里要求账号密码
BACK_TO_LIST_POINT = Point(556, 241)

# 「退出登录」确认框（标题界面点切换账号后弹出）的识别区域（1920x1080）
# 要覆盖到最下方的「取消 / 确定」按钮，否则定位不到按钮文字
LOGOUT_DIALOG_RECT = Rect(480, 200, 1300, 880)

# 选项文字左侧的多选框半径偏移：点不到文字时用它来点圆圈本身
RADIO_OFFSET_X = 30

# 确认框里「退出并保留登录记录」单选项的中心（OCR 失败时的兜底）
KEEP_RECORD_POINT = Point(632, 532)

# 确认框右下角「确定」按钮的中心（OCR 失败时的兜底）
CONFIRM_LOGOUT_POINT = Point(1143, 775)

# 标题界面「点击进入游戏」文字所在区域，用来判断当前是不是标题界面
TITLE_CLICK_RECT = Rect(780, 830, 1150, 940)

# 标题界面右下角图标列的搜索区域。图标会随镜头缓慢漂移，
# 原生 hardcode 的 (1790,965,1853,1029) 经常对不上，所以整块区域做模板匹配。
SWITCH_ICON_CROP = Rect(1720, 620, 1920, 1080)

# 登出后 miHoYo 登录页的账号行右侧有个下拉箭头（▼ 展开 / ▲ 收起），
# 默认只显示一行当前账号，必须点开才能看到全部已保存记录
ACCOUNT_DROPDOWN_CROP = Rect(1080, 380, 1400, 840)

# 登录页「进入游戏」按钮的识别区域
ENTER_GAME_RECT = Rect(850, 600, 1160, 780)

# 掩码手机号里允许出现的字符
MASK_CHARS = frozenset('0123456789*')

# OCR 常见形近字纠正，避免数字被识别成形近字母
_LOOKALIKE = str.maketrans({
    'O': '0', 'o': '0', 'D': '0',
    'I': '1', 'l': '1', '|': '1', 'i': '1',
    'Z': '2', 'S': '5', 'B': '8', 'g': '9', 'q': '9',
})

# 找不到目标账号 / 点击无效时的最多尝试次数
MAX_TRIES = 3

# 点击账号后等待游戏响应的秒数，期间不重复点击
WAIT_AFTER_CLICK = 8.0

STATUS_PATCHED = '已保存账号补丁已生效'


@dataclass
class AccountRow:
    """弹窗里的一行登录记录。"""

    items: list[tuple[int, str]] = field(default_factory=list)
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0

    @classmethod
    def from_box(cls, box: OcrMatchResult) -> 'AccountRow':
        return cls(
            items=[(box.x, box.data)],
            left=box.x,
            top=box.y,
            right=box.x + box.w,
            bottom=box.y + box.h,
        )

    def add(self, box: OcrMatchResult) -> None:
        self.items.append((box.x, box.data))
        self.left = min(self.left, box.x)
        self.top = min(self.top, box.y)
        self.right = max(self.right, box.x + box.w)
        self.bottom = max(self.bottom, box.y + box.h)

    @property
    def text(self) -> str:
        return ''.join(text for _, text in sorted(self.items))

    @property
    def digits(self) -> str:
        """把整行文本归一化成纯数字，用于和配置里的掩码账号比较。"""
        return re.sub(r'\D', '', self.text.translate(_LOOKALIKE))

    @property
    def center(self) -> Point:
        return Point((self.left + self.right) // 2, (self.top + self.bottom) // 2)

    def __repr__(self) -> str:
        return f'AccountRow(text={self.text!r}, digits={self.digits!r}, top={self.top})'


def _is_mask_box(text: str) -> bool:
    """判断一段 OCR 文本是否属于「掩码手机号」的一部分。"""
    chars = [ch for ch in text if not ch.isspace()]
    if not chars:
        return False
    mask_count = sum(1 for ch in chars if ch in MASK_CHARS)
    return mask_count >= 3 and mask_count / len(chars) >= 0.6


def extract_rows(results: list[OcrMatchResult]) -> list[AccountRow]:
    """把 OCR 结果按行聚合成登录记录。

    OCR 会把一行号码拆成多段（例如 ``138`` 和 ``*00``），所以先按 y 方向
    重叠聚合，再按 x 排序拼接成完整号码。
    """
    boxes = [r for r in results if _is_mask_box(r.data or '')]
    boxes.sort(key=lambda r: (r.y, r.x))

    rows: list[AccountRow] = []
    for box in boxes:
        for row in rows:
            overlap = min(row.bottom, box.y + box.h) - max(row.top, box.y)
            min_height = min(row.bottom - row.top, box.h)
            if overlap > 0.5 * min_height:
                row.add(box)
                break
        else:
            rows.append(AccountRow.from_box(box))

    rows.sort(key=lambda r: r.top)
    return rows


def looks_like_dialog(rows: list[AccountRow]) -> bool:
    """判断这些行是否排成账号弹窗的样子。

    弹窗里的记录左对齐、行距接近；普通画面里零散的数字不会同时满足这两点，
    用它兜底可以避免在游戏中误判。
    """
    if len(rows) < 2:
        return False
    for upper, lower in zip(rows, rows[1:]):
        if abs(upper.left - lower.left) <= 40 and 50 <= lower.top - upper.top <= 200:
            return True
    return False


def pick_row(rows: list[AccountRow], target: str) -> AccountRow | None:
    """选出目标账号所在的行。

    弹窗最上面一行是「当前账号」摘要，会和下面已保存记录显示同一个号码，
    因此同一号码出现多次时取最靠下的一行（真正的登录记录）。
    """
    target_digits = re.sub(r'\D', '', target.translate(_LOOKALIKE))
    if len(target_digits) < 5:
        # 配置明显不是手机号掩码时不做猜测，交给原生流程
        return None
    matched = [row for row in rows if row.digits == target_digits]
    if not matched:
        return None
    return max(matched, key=lambda r: r.top)


def _saved_account_of(game_account_config) -> str:
    """读取某个实例配置的目标已保存账号，未配置时返回空串。"""
    try:
        value = game_account_config.get('saved_account', '')
    except Exception:
        return ''
    if value is None:
        return ''
    return str(value).strip()


def _read_saved_account(enter_game: EnterGame) -> str:
    """读取当前实例配置的目标已保存账号。"""
    try:
        return _saved_account_of(enter_game.ctx.game_account_config)
    except Exception:
        log.error('读取 saved_account 配置失败', exc_info=True)
        return ''


def _patched_has_login_info(self) -> bool:
    """配置了已保存账号就视为有登录方式。

    原生逻辑认为「有账号密码」才叫有登录信息；没有时 EnterGame 会把
    强制重新登录降级成 False，于是直接在游戏当前账号上跑。配置了
    ``saved_account`` 时登录方式是「点选已保存记录」，同样能保证登录到
    目标账号，所以这里一并算作有登录信息。
    """
    origin = GameAccountConfig._saved_account_orig_has_login_info
    if origin.fget(self):
        return True
    return _saved_account_of(self) != ''


def _is_credential_page(texts: set[str]) -> bool:
    """判断当前画面是不是「输入手机号/邮箱 + 密码」的账号密码登录页。"""
    return any('忘记密码' in text or '输入密码' in text for text in texts)


def _is_logout_dialog(texts: set[str]) -> bool:
    """判断当前画面是不是标题界面的「退出登录」确认框。

    确认框里有两个单选项：「退出并清除登录记录」「退出并保留登录记录」。
    一条龙原生只认一个「确定」按钮，且它的『切换账号确定』区域位于
    (1036,604,1226,812)，和新版确认框的按钮位置对不上，于是一直点空、卡住。
    """
    return any('登录记录' in text and ('清除' in text or '保留' in text) for text in texts)


def _counter(op, name: str) -> int:
    return getattr(op, name, 0)


def _bump(op, name: str) -> int:
    value = getattr(op, name, 0) + 1
    setattr(op, name, value)
    return value


def _detect_page(op, screen: MatLike) -> tuple[list[AccountRow], set[str]]:
    """识别画面：返回按行聚合的登录记录，以及账号弹窗和退出确认框里的全部文本。"""
    if screen is None:
        return [], set()
    ocr_service = op.ctx.ocr_service
    dialog_results = ocr_service.get_ocr_result_list(
        screen,
        rect=DIALOG_RECT,
        crop_first=True,
    )
    logout_results = ocr_service.get_ocr_result_list(
        screen,
        rect=LOGOUT_DIALOG_RECT,
        crop_first=True,
    )
    texts = {result.data or '' for result in dialog_results}
    texts.update(result.data or '' for result in logout_results)
    return extract_rows(dialog_results), texts


def _find_text_in(op, rect: Rect, *keywords: str) -> OcrMatchResult | None:
    """在指定区域里 OCR，返回同时包含全部关键字的第一个文字框。"""
    screen = op.last_screenshot
    if screen is None:
        return None
    try:
        results = op.ctx.ocr_service.get_ocr_result_list(
            screen,
            rect=rect,
            crop_first=True,
        )
    except Exception:
        log.error('OCR 识别失败', exc_info=True)
        return None
    for result in results:
        data = result.data or ''
        if all(keyword in data for keyword in keywords):
            return result
    return None


def _handle_logout_dialog(op) -> OperationRoundResult:
    """在「退出登录」确认框里选「退出并保留登录记录」再点确定。

    选项和按钮的位置都不写死：按 OCR 识别到的文字框换算点击点，这样游戏窗口
    边框、UI 缩放或版本微调都不会点偏。
    """
    if _counter(op, '_saved_account_logout_clicks') >= MAX_TRIES:
        return op.round_fail('多次确认退出登录仍未回到账号列表')

    keep_box = _find_text_in(op, LOGOUT_DIALOG_RECT, '保留')
    confirm_box = _find_text_in(op, LOGOUT_DIALOG_RECT, '确定')

    if keep_box is not None:
        # 文字左边一点就是单选框圆圈，两个点都点一下，保证选中「保留登录记录」
        keep_point = Point(keep_box.x - RADIO_OFFSET_X, keep_box.center.y)
        op.ctx.controller.click(keep_point)
        log.info('退出确认框点选「退出并保留登录记录」 %s', keep_point)
    else:
        op.ctx.controller.click(KEEP_RECORD_POINT)
        log.warning('未识别到「退出并保留登录记录」，使用兜底坐标 %s', KEEP_RECORD_POINT)

    time.sleep(0.2)

    if confirm_box is not None:
        confirm_point = confirm_box.center
        log.info('退出确认框点击「确定」 %s', confirm_point)
    else:
        confirm_point = CONFIRM_LOGOUT_POINT
        log.warning('未识别到「确定」，使用兜底坐标 %s', confirm_point)
    op.ctx.controller.click(confirm_point)

    _bump(op, '_saved_account_logout_clicks')
    return op.round_wait('已确认退出登录（保留登录记录）', wait=3)


def _handle_credential_page(op) -> OperationRoundResult:
    """账号密码页点左上角返回，回到已保存账号列表。"""
    if _counter(op, '_saved_account_back_clicks') >= MAX_TRIES:
        return op.round_fail('无法从账号密码页返回已保存账号列表')

    _bump(op, '_saved_account_back_clicks')
    log.info('进入账号密码页，点击返回以回到已保存账号列表')
    op.ctx.controller.click(BACK_TO_LIST_POINT)
    return op.round_wait('返回已保存账号列表', wait=2)


def _handle_saved_account_dialog(op, rows: list[AccountRow], target: str) -> OperationRoundResult:
    """在已保存账号列表里点选目标账号。"""
    row = pick_row(rows, target)
    if row is None:
        _bump(op, '_saved_account_tries')
        found = '、'.join(r.digits for r in rows)
        status = f'未找到已保存账号 {target}，当前识别到: {found}'
        if _counter(op, '_saved_account_tries') > MAX_TRIES:
            return op.round_fail(status)
        return op.round_retry(status, wait=2)

    now = time.time()
    last_click = getattr(op, '_saved_account_last_click', 0.0)
    if last_click > 0 and now - last_click < WAIT_AFTER_CLICK:
        # 刚点过，等游戏反应，避免重复点击
        return op.round_wait(f'等待已保存账号 {target} 登录', wait=2)

    _bump(op, '_saved_account_clicks')
    if _counter(op, '_saved_account_clicks') > MAX_TRIES:
        found = '、'.join(r.digits for r in rows)
        return op.round_fail(f'点击已保存账号 {target} 无效，当前识别到: {found}')

    log.info('检测到已保存账号弹窗，点选 %s (位置 %s)', target, row.center)
    op.ctx.controller.click(row.center)
    op._saved_account_last_click = now
    op._saved_account_tries = 0
    op._saved_account_back_clicks = 0
    if hasattr(op, 'already_login'):
        op.already_login = True
    return op.round_wait(f'已选择保存的账号 {target}', wait=3)


def _is_title_screen(op, screen: MatLike) -> bool:
    """标题界面：画面下方有「点击进入游戏」。"""
    if screen is None:
        return False
    results = op.ctx.ocr_service.get_ocr_result_list(
        screen,
        rect=TITLE_CLICK_RECT,
        crop_first=True,
    )
    return any('点击进入游戏' in (result.data or '') for result in results)


def _find_switch_icon(op, screen: MatLike) -> Point | None:
    """在右下角区域用模板匹配找「切换账号」圆形图标。

    标题界面的图标会跟着镜头缓慢漂移，原生固定的
    (1790,965,1853,1029) 区域经常对不上，所以这里在整块右下角里找。
    """
    if screen is None:
        return None
    part = cv2_utils.crop_image_only(screen, SWITCH_ICON_CROP)
    try:
        mrl = op.ctx.tm.match_template(
            part, 'enter_game', 'switch_account', threshold=0.65
        )
    except Exception:
        log.error('匹配「切换账号」图标失败', exc_info=True)
        return None
    if mrl.max is None:
        return None
    return mrl.max.center + SWITCH_ICON_CROP.left_top


def _handle_title_switch(op) -> OperationRoundResult | None:
    """标题界面点「切换账号」图标，弹出「退出登录」确认框。"""
    if _counter(op, '_saved_account_switch_clicks') >= MAX_TRIES:
        return None
    point = _find_switch_icon(op, op.last_screenshot)
    if point is None:
        return None
    _bump(op, '_saved_account_switch_clicks')
    log.info('标题界面点击「切换账号」图标 %s', point)
    op.ctx.controller.click(point)
    return op.round_wait('点击切换账号', wait=2)


def _find_text_center(op, screen: MatLike, rect: Rect, keyword: str) -> Point | None:
    """在指定区域里 OCR，找包含关键字的文字框并返回其中心点。"""
    if screen is None:
        return None
    try:
        results = op.ctx.ocr_service.get_ocr_result_list(
            screen,
            rect=rect,
            crop_first=True,
        )
    except Exception:
        log.error('OCR 识别 %s 失败', keyword, exc_info=True)
        return None
    for result in results:
        if keyword in (result.data or ''):
            return result.center
    return None


def _find_dropdown_icon(op, screen: MatLike) -> Point | None:
    """在登录页账号行右侧找「展开账号列表」的下拉箭头。"""
    if screen is None:
        return None
    part = cv2_utils.crop_image_only(screen, ACCOUNT_DROPDOWN_CROP)
    try:
        mrl = op.ctx.tm.match_template(
            part, 'enter_game', 'account_dropdown_expand', threshold=0.75
        )
    except Exception:
        log.error('匹配账号下拉箭头失败', exc_info=True)
        return None
    if mrl.max is None:
        return None
    return mrl.max.center + ACCOUNT_DROPDOWN_CROP.left_top


def _handle_login_page(op, texts: set[str], target: str) -> OperationRoundResult | None:
    """处理登出后的 miHoYo 登录页。

    这个页面默认只显示一行当前账号（右侧带 ▼ 下拉箭头），点开箭头才会列出
    全部已保存记录；选好账号后再点「进入游戏」。原生代码完全不认识这个页面。
    """
    if not any('进入游戏' in text and '点击进入游戏' not in text for text in texts):
        return None

    if getattr(op, 'already_login', False):
        # 账号已经点选过，接下来只需要点「进入游戏」
        if _counter(op, '_saved_account_enter_clicks') >= MAX_TRIES:
            return op.round_fail('多次点击「进入游戏」仍未开始加载')
        point = _find_text_center(op, op.last_screenshot, ENTER_GAME_RECT, '进入游戏')
        if point is None:
            return None
        _bump(op, '_saved_account_enter_clicks')
        log.info('已选好账号 %s，点击「进入游戏」 %s', target, point)
        op.ctx.controller.click(point)
        return op.round_wait('点击进入游戏', wait=3)

    if _counter(op, '_saved_account_expand_clicks') >= MAX_TRIES:
        return op.round_fail('多次展开账号下拉仍未看到目标账号')

    now = time.time()
    last_expand = getattr(op, '_saved_account_last_expand', 0.0)
    if last_expand > 0 and now - last_expand < 4.0:
        # 刚点过下拉，等动画展开，避免又把它点回去
        return op.round_wait('等待账号列表展开', wait=2)

    point = _find_dropdown_icon(op, op.last_screenshot)
    if point is None:
        return None
    _bump(op, '_saved_account_expand_clicks')
    op._saved_account_last_expand = now
    log.info('登录页点击账号下拉箭头 %s', point)
    op.ctx.controller.click(point)
    return op.round_wait('展开已保存账号列表', wait=2)


def _check_saved_account_pages(op, allow_title_switch: bool = False) -> OperationRoundResult | None:
    """在进入游戏的画面识别之前，先处理与已保存账号相关的画面。

    :param allow_title_switch: 是否允许在标题界面主动点「切换账号」图标
    :return: 已处理时返回对应的轮次结果，未命中返回 None 交给原生逻辑
    """
    target = _read_saved_account(op)
    if target == '' or op.last_screenshot is None:
        return None

    rows, texts = _detect_page(op, op.last_screenshot)

    if _is_logout_dialog(texts):
        return _handle_logout_dialog(op)

    if _is_credential_page(texts):
        return _handle_credential_page(op)

    if looks_like_dialog(rows):
        return _handle_saved_account_dialog(op, rows, target)

    login_result = _handle_login_page(op, texts, target)
    if login_result is not None:
        return login_result

    if (
        allow_title_switch
        and not getattr(op, 'already_login', False)
        and getattr(op, 'force_login', False)
        and _is_title_screen(op, op.last_screenshot)
    ):
        return _handle_title_switch(op)

    return None


def _orig_of(cls, name: str):
    """取某个方法打补丁前的原始实现。

    原始函数不能直接挂成类属性：类属性里的函数会被 ``inspect.getmembers``
    当作方法扫描，带 ``@operation_node`` 注解的原函数会被重复注册成节点
    （表现为「存在重复的节点 画面识别」）。所以统一放进一个 dict 里。
    """
    store = cls.__dict__.get('_saved_account_originals')
    if store is None:
        store = {}
        setattr(cls, '_saved_account_originals', store)
    if name in store:
        return store[name]
    current = getattr(cls, name)
    # functools.wraps 会把原函数挂在 __wrapped__ 上，插件热重载时靠它找到真正的原始实现
    origin = getattr(current, '__wrapped__', current)
    store[name] = origin
    return origin


def _patched_check_login_related(
    self: EnterGame, screen: MatLike
) -> OperationRoundResult | None:
    result = _check_saved_account_pages(self)
    if result is not None:
        return result
    return _orig_of(EnterGame, 'check_login_related')(self, screen)


def _patched_handle_init(self: EnterGame) -> None:
    _orig_of(EnterGame, 'handle_init')(self)
    self._saved_account_tries = 0
    self._saved_account_clicks = 0
    self._saved_account_back_clicks = 0
    self._saved_account_logout_clicks = 0
    self._saved_account_switch_clicks = 0
    self._saved_account_expand_clicks = 0
    self._saved_account_enter_clicks = 0
    self._saved_account_last_expand = 0.0
    self._saved_account_last_click = 0.0


def _patched_entergame_init(self: EnterGame, ctx, switch: bool = False) -> None:
    """配置了已保存账号时，进入游戏前一定先选号。

    原生逻辑只有「强制重新登录」打开时才会主动点切换账号。配了
    ``saved_account`` 说明用户希望每次进入游戏都用这个账号，所以这里
    直接把 ``force_login`` 打开（switch=True 的切换账号流程本来就要求登录）。
    """
    _orig_of(EnterGame, '__init__')(self, ctx, switch)
    if switch or self.force_login:
        return
    try:
        game_account_config = ctx.game_account_config
    except Exception:
        return
    if _saved_account_of(game_account_config) != '':
        self.force_login = True


def _impl_more_logout_confirm(op) -> OperationRoundResult | None:
    """切换账号流程里的「更多登出确认」节点。

    新版游戏的退出确认框是「退出并清除/保留登录记录 + 取消/确定」，原生只认
    一个文字为「确认」的按钮，对不上就一直重试，所以这里直接按确认框处理。
    """
    if _read_saved_account(op) == '':
        return None
    _, texts = _detect_page(op, op.last_screenshot)
    if _is_logout_dialog(texts):
        return _handle_logout_dialog(op)
    return None


def _impl_wait_switch_can_click(op) -> OperationRoundResult | None:
    """切换账号流程里的「等待切换账号可按」节点。

    登出后游戏直接弹已保存账号列表，标题界面的「点击进入游戏」会被弹窗盖住，
    原生在这里会一直重试 20 次然后失败，所以弹窗一出现就直接放行给 EnterGame。
    """
    if _read_saved_account(op) == '':
        return None
    rows, texts = _detect_page(op, op.last_screenshot)
    if looks_like_dialog(rows):
        return op.round_success('已出现已保存账号列表', wait=1)
    if _is_logout_dialog(texts) or _is_credential_page(texts):
        return op.round_success('已出现账号相关的确认页', wait=1)
    return None


def _make_patched_node(origin, impl):
    """包装一个 @operation_node 方法，并保留节点/连线注解。

    一条龙的节点网络是用 inspect.getmembers 取实例方法、再读方法上的
    ``operation_node_annotation`` 建立的，直接替换类属性会丢掉节点注解，
    导致「找不到起始节点」。
    """

    @functools.wraps(origin)
    def patched(self) -> OperationRoundResult:
        result = impl(self)
        if result is not None:
            return result
        return origin(self)

    node = getattr(origin, 'operation_node_annotation', None)
    if node is not None:
        new_node = copy.copy(node)
        new_node.op_method = patched
        patched.operation_node_annotation = new_node
    return patched


def apply_patch() -> bool:
    """给 EnterGame / SwitchAccount 打补丁；重复调用（插件热重载）不会叠加包裹。"""
    # 原始函数存在类的 _saved_account_originals 字典里；直接存成类属性会被
    # inspect.getmembers 当成方法扫描，带节点注解的原函数会被重复注册成节点
    orig_screen = _orig_of(EnterGame, 'check_screen')
    orig_check = _orig_of(EnterGame, 'check_login_related')
    orig_init = _orig_of(EnterGame, 'handle_init')
    orig_entergame_init = _orig_of(EnterGame, '__init__')
    orig_logout_confirm = _orig_of(SwitchAccount, 'more_logout_confirm')
    orig_wait_switch = _orig_of(SwitchAccount, 'wait_switch_can_click')

    orig_has_login_info = getattr(
        GameAccountConfig, '_saved_account_orig_has_login_info', None
    )
    if orig_has_login_info is None:
        orig_has_login_info = GameAccountConfig.has_login_info
        GameAccountConfig._saved_account_orig_has_login_info = orig_has_login_info
    # 避免未使用的变量告警，同时保留引用便于调试
    _ = (orig_check, orig_init, orig_entergame_init)

    # 热重载后重新安装本模块的最新实现，而不是保留旧函数对象
    EnterGame.__init__ = _patched_entergame_init
    EnterGame.check_login_related = _patched_check_login_related
    EnterGame.check_screen = _make_patched_node(
        orig_screen, lambda op: _check_saved_account_pages(op, allow_title_switch=True)
    )
    EnterGame.handle_init = _patched_handle_init
    SwitchAccount.more_logout_confirm = _make_patched_node(
        orig_logout_confirm, _impl_more_logout_confirm
    )
    SwitchAccount.wait_switch_can_click = _make_patched_node(
        orig_wait_switch, _impl_wait_switch_can_click
    )
    GameAccountConfig.has_login_info = property(_patched_has_login_info)
    EnterGame._saved_account_patched = True
    log.info(STATUS_PATCHED)
    return True


