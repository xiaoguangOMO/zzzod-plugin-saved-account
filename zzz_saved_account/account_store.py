"""目标账号配置的存取。

数据放在一条龙给插件准备的目录：``plugin_data/zzz_saved_account/targets.json``。

为什么不写在主程序的实例配置里
------------------------------
旧版本（<= 1.2.0）把 ``saved_account`` 写在 ``config/<实例>/game_account.yml``。
那个文件是主程序自己的账号配置，和明文账号密码放在一起：插件卸载后字段还在，
用户备份配置也会把它一起带走，插件不应该往里塞自己的字段。

读取时如果发现实例配置里还有旧字段，会自动迁移到插件数据目录，并把旧字段删掉。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from one_dragon.utils import os_utils
from one_dragon.utils.log_utils import log

try:  # 商店/模板布局：插件包内有 const.py
    from .const import APP_ID
except ImportError:  # 扁平布局：插件包内有 zzz_saved_account_const.py
    from .zzz_saved_account_const import APP_ID

FILE_NAME = 'targets.json'
"""插件数据目录下的配置文件名"""

_LEGACY_KEY = 'saved_account'
"""旧版本写在主程序实例账号配置里的字段名"""

_data_file_path: str | None = None
"""本次进程内解析出的配置文件路径"""


def _data_file() -> Path:
    global _data_file_path
    if _data_file_path is None:
        # 注意：不能用 get_path_under_work_dir 直接拼到文件名——那个函数会把
        # 整条路径当目录创建出来，文件名会变成一个目录。
        data_dir = os_utils.get_path_under_work_dir('plugin_data', APP_ID)
        _data_file_path = str(Path(data_dir) / FILE_NAME)
    return Path(_data_file_path)


def _load() -> dict[str, str]:
    path = _data_file()
    if not path.is_file():
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as fp:
            raw = json.load(fp)
    except Exception:
        log.error(f'读取目标账号配置失败: {path}', exc_info=True)
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in raw.items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            result[str(key)] = text
    return result


def _save(data: dict[str, str]) -> bool:
    path = _data_file()
    tmp_path = path.with_name(path.name + '.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, 'w', encoding='utf-8') as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2, sort_keys=True)
            fp.write('\n')
    except Exception:
        log.error(f'写入目标账号配置失败: {path}', exc_info=True)
        _remove_file(tmp_path)
        return False

    # 杀毒软件/索引服务偶尔会短暂占用刚写入的文件，重试几次比直接失败稳妥
    for attempt in range(3):
        try:
            os.replace(tmp_path, path)
            return True
        except Exception:
            if attempt == 2:
                log.error(f'保存目标账号配置失败: {path}', exc_info=True)
                _remove_file(tmp_path)
                return False
            time.sleep(0.05)
    return False


def _remove_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def _migrate_legacy(instance_idx: int) -> str:
    """把旧版本写在 game_account.yml 里的目标账号搬到插件数据目录。

    只在读不到插件目录里的值时调用；迁移成功后删掉旧字段，避免继续留在
    主程序的实例配置里被一起备份/带走。
    """
    try:
        from one_dragon.base.config.game_account_config import GameAccountConfig

        config = GameAccountConfig(instance_idx)
        value = config.get(_LEGACY_KEY, '')
        text = '' if value is None else str(value).strip()
        if not text:
            return ''
        data = _load()
        data[str(instance_idx)] = text
        if not _save(data):
            # 写不进插件目录时不要动旧字段，否则这份配置就丢了
            return text
        if isinstance(config.data, dict):
            config.data.pop(_LEGACY_KEY, None)
            config.save()
        log.info(f'实例 {instance_idx:02d} 的目标账号已迁移到插件数据目录')
        return text
    except Exception:
        log.error(f'迁移实例 {instance_idx:02d} 的目标账号失败', exc_info=True)
        return ''


def get_target(instance_idx: int | None) -> str:
    """读取某个实例的目标账号掩码，没有配置时返回空串。"""
    if instance_idx is None:
        return ''
    try:
        key = str(instance_idx)
        data = _load()
        if key in data:
            return data[key]
        return _migrate_legacy(instance_idx)
    except Exception:
        log.error(f'读取实例 {instance_idx:02d} 的目标账号失败', exc_info=True)
        return ''


def set_target(instance_idx: int | None, value: str) -> None:
    """写入某个实例的目标账号掩码；传空串表示该实例不启用切号。"""
    if instance_idx is None:
        return
    key = str(instance_idx)
    text = (value or '').strip()
    data = _load()
    if text:
        data[key] = text
    else:
        data.pop(key, None)
    _save(data)


def mask_digits(text: str) -> str:
    """日志用的账号占位符：不输出任何数字，只说明位数。

    日志会被用户贴到公开 issue 里排查问题，所以目标账号和画面上识别到的
    其它账号都不应该落进 ``.log/log.txt``。
    """
    digits = ''.join(ch for ch in str(text or '') if ch.isdigit())
    if not digits:
        return '***'
    return f'***（{len(digits)} 位）'
