"""一条龙第三方插件：已保存账号免密切换。

插件本身不注册游戏内的自动化应用，作用是在被一条龙加载时给「进入游戏」流程
打补丁：当游戏弹出已保存的登录记录时，直接点选当前实例配置的目标账号，
免去退出后重新输入账号密码的步骤。

插件详情里的使用说明由插件商店的条目提供（上架后自动抓取仓库 README），
插件本身不改主程序的任何界面类。
"""

from one_dragon.utils.log_utils import log

try:
    from .saved_account_patch import apply_patch

    apply_patch()
except Exception:
    # 不能因为补丁失败就阻止整个插件被注册，否则连状态页都看不到
    log.error('已保存账号补丁加载失败', exc_info=True)
