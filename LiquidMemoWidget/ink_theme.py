"""Chrome tinting for the 灵动水墨 (ink-wash) skin.

The ink skin keeps the app's themed personality: while it is the active skin, the settings /
history windows, add popup, tray menu, round buttons and the floating launcher bubble all tint
to the selected 水墨主题 instead of the default blue. Choosing another skin restores the normal
look. There is no activation state — `active` simply mirrors `settings.skin == "ink"`.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QApplication
from qfluentwidgets import setThemeColor

from ink_swirl import SwirlThemeTokens, swirl_tokens


NORMAL_ACCENT = "#009FAA"


def _darken(hex_color: str, factor: float) -> str:
    """Return `hex_color` scaled toward black by `factor` (0.0 = unchanged)."""
    value = int(hex_color.lstrip("#"), 16)
    channels = [round(component * (1.0 - factor)) for component in (value >> 16 & 0xFF, value >> 8 & 0xFF, value & 0xFF)]
    return "#{:02X}{:02X}{:02X}".format(*channels)


@dataclass(frozen=True)
class InkTheme:
    """Colours one selectable 水墨主题 lends to the chrome while the ink skin is active."""

    key: str
    label: str                       # 设置 → 外观 → 水墨主题 的显示名
    accent: str                      # Fluent 控件主色 / 加号·确认按钮 / 托盘菜单选中
    text: str                        # 染色面板上的正文色
    muted: str                       # 次级文字
    panel_top: str                   # 设置 / 历史窗口面板渐变（浅色调）
    panel_bottom: str
    popup_bg: str                    # 添加弹窗背景基色
    bubble: tuple[str, str, str]     # 悬浮球渐变三段

    @property
    def accent_rgb(self) -> str:
        value = int(self.accent.lstrip("#"), 16)
        return f"{value >> 16 & 0xFF},{value >> 8 & 0xFF},{value & 0xFF}"

    @property
    def nav_text(self) -> str:
        return _darken(self.accent, 0.35)


INK_THEMES: dict[str, InkTheme] = {
    "qinghua": InkTheme(
        key="qinghua", label="青花 · 蓝", accent="#3D7AB3", text="#143B61", muted="#6B7F93",
        panel_top="#F7FAFF", panel_bottom="#E3EEFB", popup_bg="#F0F7FF",
        bubble=("#79E4FF", "#64B8FF", "#756BFF"),
    ),
    "warm": InkTheme(
        key="warm", label="暖玉 · 暖", accent="#C08A6A", text="#43302F", muted="#8A6F5E",
        panel_top="#FBF6EC", panel_bottom="#F1E3CD", popup_bg="#FBF5EB",
        bubble=("#FFD9A8", "#F2AE72", "#D97C5A"),
    ),
    "blush": InkTheme(
        key="blush", label="黛粉 · 粉", accent="#C96A86", text="#4A2334", muted="#8C6574",
        panel_top="#FFF8FB", panel_bottom="#FFE3EC", popup_bg="#FFF0F6",
        bubble=("#FFB7D5", "#FF78B2", "#C68CFF"),
    ),
}

DEFAULT_INK_THEME = "qinghua"


def ink_theme(key: str) -> InkTheme:
    return INK_THEMES.get(key, INK_THEMES[DEFAULT_INK_THEME])


def ink_swirl_tokens(key: str) -> SwirlThemeTokens:
    """Readability tokens for the ink skin's background, keyed like INK_THEMES."""
    return swirl_tokens(key)


class InkThemeController:
    """Apply / restore the chrome tint. Like the other app managers it is stopped on shutdown,
    but it owns no timers — `stop` exists for call-site parity only."""

    def __init__(self, app) -> None:
        self.app = app

    @property
    def active(self) -> bool:
        return self.app.state.settings.skin == "ink"

    @property
    def theme(self) -> InkTheme:
        return ink_theme(self.app.state.settings.inkTheme)

    def bind_ui(self) -> None:
        self.apply_theme()

    def stop(self) -> None:
        pass

    def apply_theme(self) -> None:
        active = self.active
        theme = self.theme
        qt = QApplication.instance()
        if qt is not None:
            qt.setProperty("inkMode", active)
        setThemeColor(theme.accent if active else NORMAL_ACCENT, save=False)
        floating = getattr(self.app, "floating", None)
        if floating is not None:
            floating.launcher.set_ink_mode(active, theme.bubble)
        # window / settings_window / history_window are all parent-less top-level widgets exposing
        # apply_ink_theme, so this single sweep covers them — no separate named loop needed.
        for widget in QApplication.topLevelWidgets():
            if hasattr(widget, "apply_ink_theme"):
                widget.apply_ink_theme(theme if active else None)
