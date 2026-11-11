"""已保存账号免密切换 —— 设置弹窗。

按实例列出目标账号掩码输入框，改完失焦即写入该实例的
``config/<实例号>/game_account.yml``（字段 ``saved_account``），
不需要再手动编辑 yml。
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout
from qfluentwidgets import BodyLabel

from one_dragon.base.config.game_account_config import GameAccountConfig
from one_dragon.utils.log_utils import log
from one_dragon_qt.widgets.app_setting.app_setting_flyout import AppSettingFlyout
from one_dragon_qt.widgets.setting_card.text_setting_card import TextSettingCard


class SavedAccountSettingFlyout(AppSettingFlyout):
    """每个实例一行：填写该实例要登录的已保存账号掩码。"""

    def _setup_ui(self, layout: QVBoxLayout) -> None:
        self._cards: dict[int, TextSettingCard] = {}

        hint = BodyLabel(
            '为每个实例填写目标账号的掩码（例如 138******00），留空表示该实例不启用切号。'
            '修改后自动保存，当前实例立即生效；其它实例在切换过去时生效。'
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
            value = GameAccountConfig(instance_idx).get('saved_account', '')
        except Exception:
            log.error(f'读取实例 {instance_idx:02d} 的账号配置失败', exc_info=True)
            return ''
        return '' if value is None else str(value).strip()

    def _save(self, instance_idx: int, value: str) -> None:
        text = (value or '').strip()
        try:
            GameAccountConfig(instance_idx).update('saved_account', text)
        except Exception:
            log.error(f'保存实例 {instance_idx:02d} 的目标账号失败', exc_info=True)
            return

        # 当前实例的配置对象是 cached_property，清掉缓存让补丁读到新值
        if instance_idx == self.ctx.current_instance_idx:
            self.ctx.__dict__.pop('game_account_config', None)

        log.info(
            f'实例 {instance_idx:02d} 的目标账号已保存: {text or "（空，不启用）"}'
        )
