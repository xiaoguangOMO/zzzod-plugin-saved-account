"""已保存账号免密切换 —— 设置弹窗。

按实例列出目标账号掩码输入框，改完失焦即写入插件自己的数据目录
（``plugin_data/zzz_saved_account/targets.json``），不需要手动编辑任何文件。
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout
from qfluentwidgets import BodyLabel

from one_dragon.utils.log_utils import log
from one_dragon_qt.widgets.app_setting.app_setting_flyout import AppSettingFlyout
from one_dragon_qt.widgets.setting_card.text_setting_card import TextSettingCard

from . import account_store


class SavedAccountSettingFlyout(AppSettingFlyout):
    """每个实例一行：填写该实例要登录的已保存账号掩码。"""

    def _setup_ui(self, layout: QVBoxLayout) -> None:
        self._cards: dict[int, TextSettingCard] = {}

        hint = BodyLabel(
            '为每个实例填写目标账号的掩码（例如 138******00），留空表示该实例不启用切号。'
            '修改后自动保存，当前实例立即生效；其它实例在切换过去时生效。'
            '这些掩码存在 plugin_data/zzz_saved_account/targets.json，不会写进主程序的账号配置。'
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        for instance in self.ctx.one_dragon_config.instance_list:
            suffix = '（当前实例）' if instance.idx == self.ctx.current_instance_idx else ''
            card = TextSettingCard(
                icon='',
                title=f'实例 {instance.idx:02d} · {instance.name}{suffix}',
                content='目标账号掩码',
                margins=self.card_margins,
                input_placeholder='例如 138******00',
            )
            card.setValue(self._read(instance.idx), emit_signal=False)
            card.value_changed.connect(
                lambda value, idx=instance.idx: self._save(idx, value)
            )
            layout.addWidget(card)
            self._cards[instance.idx] = card

    def init_config(self) -> None:
        for instance in self.ctx.one_dragon_config.instance_list:
            card = self._cards.get(instance.idx)
            if card is not None:
                card.setValue(self._read(instance.idx), emit_signal=False)

    @staticmethod
    def _read(instance_idx: int) -> str:
        try:
            return account_store.get_target(instance_idx)
        except Exception:
            log.error(f'读取实例 {instance_idx:02d} 的目标账号失败', exc_info=True)
            return ''

    def _save(self, instance_idx: int, value: str) -> None:
        text = (value or '').strip()
        try:
            account_store.set_target(instance_idx, text)
        except Exception:
            log.error(f'保存实例 {instance_idx:02d} 的目标账号失败', exc_info=True)
            return

        # 当前实例的配置对象是 cached_property，清掉缓存让补丁读到新值
        if instance_idx == self.ctx.current_instance_idx:
            self.ctx.__dict__.pop('game_account_config', None)

        log.info(f'实例 {instance_idx:02d} 的目标账号已保存')
