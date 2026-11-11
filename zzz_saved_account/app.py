"""已保存账号免密切换的应用入口。

在一条龙的「应用运行」里点这个应用，会直接把游戏切到当前实例配置的账号上：

- 游戏没开：拉起游戏，再到标题界面选号进游戏；
- 游戏停在标题界面 / 账号弹窗：直接选号进游戏；
- 游戏已经在大世界：从菜单登出（保留登录记录），回到账号列表后再选号进游戏。

真正的识别与点击逻辑都在 ``saved_account_patch`` 里，这里只负责串流程。
"""

from one_dragon.base.operation.application_base import Application
from one_dragon.base.operation.operation_node import operation_node
from one_dragon.base.operation.operation_round_result import OperationRoundResult
from one_dragon.utils.log_utils import log
from zzz_od.context.zzz_context import ZContext
from zzz_od.operation.enter_game.enter_game import EnterGame
from zzz_od.operation.enter_game.open_game import OpenGame
from zzz_od.operation.enter_game.switch_account import SwitchAccount

from .const import APP_ID
from .saved_account_patch import _read_saved_account


class SavedAccountApp(Application):

    def __init__(self, ctx: ZContext, app_id: str = APP_ID):
        self._enter_game = EnterGame(ctx, switch=True)
        self._switch_account = SwitchAccount(ctx)
        Application.__init__(
            self,
            ctx,
            app_id,
            node_max_retry_times=1,
            op_name='已保存账号免密切换',
            need_check_game_win=True,
            op_to_enter_game=OpenGame(ctx),
            use_factory_run_record=False,
        )

    @operation_node(name='切换到目标账号', is_start_node=True)
    def switch_to_saved_account(self) -> OperationRoundResult:
        patched = getattr(EnterGame, '_saved_account_patched', False)
        if not patched:
            return self.round_fail('补丁未生效，请重启一条龙让插件重新加载')

        target = _read_saved_account(self)
        if target == '':
            return self.round_fail(
                f'当前实例（{self.ctx.current_instance_idx:02d}）未配置 saved_account，'
                f'请在 config/{self.ctx.current_instance_idx:02d}/game_account.yml 里填写'
            )

        log.info('准备把游戏切换到已保存账号 %s', target)

        if self._enter_game.is_in_big_world(self.last_screenshot) is not None:
            log.info('游戏已在大世界，先登出再选择账号')
            return self.round_by_op_result(self._switch_account.execute())

        return self.round_by_op_result(self._enter_game.execute())
