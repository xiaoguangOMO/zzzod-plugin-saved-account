from __future__ import annotations

from typing import TYPE_CHECKING

from one_dragon.base.operation.application.application_factory import (
    ApplicationFactory,
)
from one_dragon.base.operation.application_base import Application

from . import const
from .app import SavedAccountApp

if TYPE_CHECKING:
    from zzz_od.context.zzz_context import ZContext


class SavedAccountFactory(ApplicationFactory):
    """已保存账号免密切换插件工厂。"""

    def __init__(self, ctx: ZContext):
        ApplicationFactory.__init__(self, const)
        self.ctx: ZContext = ctx

    def create_application(self, instance_idx: int, group_id: str) -> Application:
        return SavedAccountApp(self.ctx)
