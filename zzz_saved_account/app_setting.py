"""已保存账号免密切换 —— 设置页声明。

框架扫描插件目录下 *_app_setting.py 后，会在「应用运行」里该应用卡片上显示齿轮按钮，
点开就是下面这个 Flyout。
"""

from __future__ import annotations

from one_dragon_qt.services.app_setting.app_setting_provider import (
    AppSettingProvider,
    SettingType,
)

try:  # 发布仓库布局：const.py
    from .const import APP_ID
except ImportError:  # 兼容旧目录布局：zzz_saved_account_const.py
    from .zzz_saved_account_const import APP_ID


class SavedAccountAppSetting(AppSettingProvider):
    """为「已保存账号免密切换」提供设置弹窗。"""

    app_id = APP_ID
    setting_type = SettingType.FLYOUT

    @staticmethod
    def get_setting_cls() -> type:
        from .setting_flyout import SavedAccountSettingFlyout

        return SavedAccountSettingFlyout
