"""Shared UI foundation: fonts, colors, geometry-free helpers and the small
widgets/mixins reused across the settings, update and memo surfaces. Leaf module
— it must not import the app, the windows or the D3D engine."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QHBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ColorDialog,
    FluentIcon,
    ToolTipFilter,
    ToolTipPosition,
    TransparentToolButton,
    setCustomStyleSheet,
)
from qfluentwidgets.components.widgets.switch_button import Indicator, IndicatorPosition, SwitchButton

ROOT = Path(__file__).resolve().parents[1]


CJK_FONT = "Microsoft YaHei"
LATIN_FONT = "Times New Roman"
FONT_STACK_QSS = 'font-family: "Times New Roman", "Microsoft YaHei", "Segoe UI Emoji";'
def qcolor(hex_value: str, fallback: str = "#111820") -> QColor:
    color = QColor(hex_value)
    return color if color.isValid() else QColor(fallback)


def mixed_font(point_size: int = 10, weight: QFont.Weight = QFont.Normal) -> QFont:
    font = QFont(LATIN_FONT, point_size, weight)
    if hasattr(font, "setFamilies"):
        font.setFamilies([LATIN_FONT, CJK_FONT, "Segoe UI Emoji"])
    return font


def mixed_font_px(pixel_size: int, weight: QFont.Weight = QFont.Normal) -> QFont:
    font = QFont(LATIN_FONT, -1, weight)
    font.setPixelSize(pixel_size)
    if hasattr(font, "setFamilies"):
        font.setFamilies([LATIN_FONT, CJK_FONT, "Segoe UI Emoji"])
    return font


def qcolor_to_rgb(color: QColor) -> tuple[int, int, int]:
    return color.red(), color.green(), color.blue()


def css_rgba(color: QColor, alpha: float = 1.0) -> str:
    alpha = max(0.0, min(1.0, alpha))
    return f"rgba({color.red()},{color.green()},{color.blue()},{int(alpha * 255)})"


def relative_luminance(color: QColor) -> float:
    def channel(value: int) -> float:
        normalized = value / 255
        return normalized / 12.92 if normalized <= 0.03928 else ((normalized + 0.055) / 1.055) ** 2.4

    red, green, blue = qcolor_to_rgb(color)
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast_ratio(foreground: QColor, background: QColor) -> float:
    first = relative_luminance(foreground)
    second = relative_luminance(background)
    lighter = max(first, second)
    darker = min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def best_contrast_color(background: QColor, candidates: list[str]) -> QColor:
    colors = [qcolor(candidate) for candidate in candidates]
    return max(colors, key=lambda color: contrast_ratio(color, background))


def blend_colors(base: QColor, overlay: QColor, amount: float) -> QColor:
    amount = max(0.0, min(1.0, amount))
    inverse = 1.0 - amount
    return QColor(
        round(base.red() * inverse + overlay.red() * amount),
        round(base.green() * inverse + overlay.green() * amount),
        round(base.blue() * inverse + overlay.blue() * amount),
    )


def add_soft_shadow(widget: QWidget, blur: int = 28, y: int = 10, alpha: int = 72) -> None:
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setOffset(0, y)
    shadow.setColor(QColor(20, 28, 36, alpha))
    widget.setGraphicsEffect(shadow)


def tray_icon() -> QIcon:
    ico_path = ROOT / "assets" / "logo.ico"
    if ico_path.exists():
        icon = QIcon(str(ico_path))
        if not icon.isNull():
            return icon
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(10, 8, 44, 48, 16, 16)
    painter.fillPath(path, QColor(248, 252, 255, 230))
    painter.setPen(QColor(255, 255, 255, 210))
    painter.drawPath(path)
    painter.setPen(QColor(28, 37, 45))
    painter.setFont(mixed_font(24, QFont.Bold))
    painter.drawText(QRect(10, 8, 44, 48), Qt.AlignCenter, "✓")
    painter.end()
    return QIcon(pixmap)
# Settings / dialog typography. Chinese renders in Microsoft YaHei, Latin in Times New Roman
# (a serif face — sizes are kept generous so it stays readable), and nothing is bold per the
# requested style; visual hierarchy comes from size alone.
SETTING_TITLE_FONT_PX = 34        # 主标题：设置 / 历史记录
SETTING_NAV_FONT_PX = 28          # 左侧分类：外观 / 行为 / 日历订阅 / 关于
SETTING_ROW_TITLE_FONT_PX = 27    # 设置项标题：皮肤 / 窗口颜色 / 窗口模式 …
SETTING_TIP_FONT_PX = 27          # 感叹号悬浮说明气泡
SETTING_CONTROL_FONT_PX = 27      # 下拉框 / 开关 / 按钮 / 颜色等控件（含下拉弹出菜单条目）
SETTING_STATUS_FONT_PX = 26       # 副标题 / 状态 / 说明性文字
POPUP_INPUT_FONT_PX = 19          # 添加备忘 / 编辑截止时间 弹窗输入框


def scaled_dialog_size(
    base_width: int,
    base_height: int,
    scale: float = 1.5,
    available: QRect | None = None,
) -> QSize:
    """Return a generously scaled dialog size that still fits the current work area.

    The 1.5x target is used whenever the screen allows it. On smaller displays the dialog is
    capped to 98% of the available work area so its frameless close controls never land behind
    the taskbar or outside the screen.
    """
    if available is None:
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)
    wanted_width = round(base_width * scale)
    wanted_height = round(base_height * scale)
    max_width = max(640, int(available.width() * 0.98))
    max_height = max(600, int(available.height() * 0.98))
    return QSize(min(wanted_width, max_width), min(wanted_height, max_height))


def enlarge_control_font(widget: QWidget, px: int = SETTING_CONTROL_FONT_PX) -> None:
    # qfluentwidgets controls hardcode `font: 14px` in their own QSS, which beats setFont;
    # appending custom QSS via setCustomStyleSheet is the supported override path. The
    # selector must be the widget's class name — a universal `*` rule loses to the default
    # type selectors on specificity.
    name = type(widget).__name__
    font = f"font: {px}px 'Times New Roman','Microsoft YaHei','Segoe UI Emoji';"
    qss = f"{name} {{ {font} }} {name} * {{ {font} }} {name} QLabel {{ {font} }}"
    setCustomStyleSheet(widget, qss, qss)
    widget.setMinimumHeight(max(widget.minimumHeight(), round(px * 2.3)))


class LargeSwitchIndicator(Indicator):
    """qfluentwidgets paints its 42x22 pill with hardcoded geometry (circle at y=5, 12px
    diameter, slider travel 5→25), so font scaling can't grow it. Redraw everything at 2x."""

    INDICATOR_W, INDICATOR_H = 84, 44
    CIRCLE_D = 24
    MARGIN = 10  # unchecked/checked pill inset: x = 10 / 50 (50 + 24 + 10 = 84)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFixedSize(self.INDICATOR_W, self.INDICATOR_H)

    def _toggleSlider(self):
        # Property sliderX keeps the base class's 5→25 range (its setter clamps min at 5);
        # paint maps it linearly onto the large pill: x_px = sliderX * 2.
        self.slideAni.setEndValue(25 if self.isChecked() else 5)
        self.slideAni.start()

    def _drawCircle(self, painter: QPainter):
        # This override REPLACES the base method, so it must redo its pen/brush setup —
        # the state left by _drawBackground is the teal pill fill (an invisible knob).
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._sliderColor())
        painter.drawEllipse(int(self.sliderX) * 2, self.MARGIN, self.CIRCLE_D, self.CIRCLE_D)


class LargeSwitchButton(SwitchButton):
    """SwitchButton with the 2x indicator swapped in, keeping the whole-widget click-to-toggle,
    On/Off label and checkedChanged signal of the original."""

    def __init__(self, parent: QWidget | None = None, indicatorPos: IndicatorPosition = IndicatorPosition.LEFT) -> None:
        super().__init__(parent, indicatorPos)
        old = self.indicator
        checked = old.isChecked()
        self.indicator = LargeSwitchIndicator(self)
        self.indicator.setChecked(checked)
        # Re-wire what __initWidget had connected to the stock indicator.
        self.indicator.toggled.connect(self._updateText)
        self.indicator.toggled.connect(self.checkedChanged)
        self.hBox.insertWidget(self.hBox.indexOf(old), self.indicator)
        self.hBox.removeWidget(old)
        old.deleteLater()
        self.setFixedHeight(LargeSwitchIndicator.INDICATOR_H)


def set_label_font(label: QWidget, px: int, weight: QFont.Weight = QFont.Normal, color: str | None = None) -> None:
    # Force a fluent label (TitleLabel/BodyLabel) to a specific pixel size and weight. These
    # labels carry their own bold QSS, so — like enlarge_control_font — the class-name custom
    # stylesheet is what actually wins; setFont keeps QFontMetrics (wrapping) in sync.
    # setCustomStyleSheet is not cosmetic: qfluentwidgets re-applies its base qss (14px) every
    # time setThemeColor/theme fires, which wipes a plain setStyleSheet override back to the
    # library size. The custom sheet is re-merged after each reapply, so it survives.
    label.setFont(mixed_font_px(px, weight))
    name = type(label).__name__
    bold = "bold " if weight >= QFont.DemiBold else ""
    extra = f" color: {color};" if color else ""
    qss = f"{name} {{ font: {bold}{px}px 'Times New Roman','Microsoft YaHei','Segoe UI Emoji';{extra} }}"
    setCustomStyleSheet(label, qss, qss)


class LargeColorDialog(ColorDialog):
    """qfluentwidgets 的 ColorDialog 是一块 488x696 的固定画布：子控件用绝对坐标摆放、
    字号 14px 烧死在库的 QSS 里，只放大字体必然溢出。此子类按应用的字号体系整体放大
    控件尺寸并重新排版。"""

    def __init__(self, color, title: str, parent=None, enableAlpha: bool = False):
        super().__init__(color, title, parent, enableAlpha)
        self._relayout_large()

    def _relayout_large(self) -> None:
        family = "'Times New Roman','Microsoft YaHei','Segoe UI Emoji'"
        # The stock dialog follows qfluentwidgets' own theme — driven by a global config this
        # app never sets, so it can render as a black panel that clashes with the light UI.
        # Pin an explicit light palette + app typography on the dialog; widget-level rules
        # always beat the library's application-level QSS.
        self.setStyleSheet(
            f"""
            #centerWidget {{ background: #fbfcfe; border-radius: 14px; }}
            #titleLabel {{ font: 30px {family}; color: #111820; }}
            QLabel {{ font: 24px {family}; color: #2a3644; background: transparent; }}
            #prefixLabel {{ font: 20px {family}; color: #7c8794; }}
            LineEdit {{
                font: 22px {family}; color: #111820;
                background: rgba(255,255,255,235);
                border: 1px solid rgba(20,30,40,55); border-radius: 10px;
            }}
            PrimaryPushButton {{
                font: 24px {family}; color: white; background: #e85d93;
                border: none; border-radius: 10px;
            }}
            PrimaryPushButton:hover {{ background: #d94f86; }}
            QPushButton {{
                font: 24px {family}; color: #2a3644; background: #ffffff;
                border: 1px solid rgba(20,30,40,55); border-radius: 10px;
            }}
            QPushButton:hover {{ background: #f4f6f9; }}
            """
        )
        # Localize the stock English labels.
        self.editLabel.setText("编辑颜色")
        self.redLabel.setText("红 R")
        self.greenLabel.setText("绿 G")
        self.blueLabel.setText("蓝 B")
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
        # Direct per-widget sheets: qfluentwidgets applies its own theme sheet ON the button
        # itself, which outranks any ancestor stylesheet — the only way to win is to replace
        # the widget's own sheet. The center panel must be opaque here because the mask dialog
        # dims everything behind it (dark when the system app theme is dark).
        self.widget.setStyleSheet(
            "#centerWidget { background: #fbfcfe; border: 1px solid rgba(20,30,40,45); border-radius: 14px; }"
        )
        self.scrollWidget.setStyleSheet("background: #fbfcfe;")
        self.scrollArea.setStyleSheet("background: #fbfcfe; border: none;")
        self.scrollArea.viewport().setStyleSheet("background: #fbfcfe;")
        self.yesButton.setStyleSheet(
            f"""
            PrimaryPushButton {{
                {FONT_STACK_QSS}
                font-size: 24px; color: white; background: #e85d93;
                border: none; border-radius: 10px;
            }}
            PrimaryPushButton:hover {{ background: #d94f86; }}
            PrimaryPushButton:pressed {{ background: #c7477a; }}
            """
        )
        widget = self.widget
        # The mask dialog's layout stretches self.widget, and the stock dialog relies on its
        # maximum size (488x696) to keep the canvas centered — raise the cap to the new size
        # rather than removing it, or the dialog fills the whole window.
        widget.setMaximumSize(600, 936)
        widget.resize(600, 872)
        self.scrollWidget.resize(560, 716)
        self.scrollArea.setViewportMargins(48, 32, 0, 32)

        # setFont keeps fontMetrics in sync so the adjustSize() calls below measure with the
        # new sizes; the stylesheet above governs the painted result.
        for label, px in ((self.titleLabel, 30), (self.editLabel, 24)):
            label.setFont(mixed_font_px(px))
            label.adjustSize()
        for label in (self.redLabel, self.greenLabel, self.blueLabel, self.opacityLabel):
            label.setFont(mixed_font_px(24))
        self.huePanel.setFixedSize(330, 330)
        self.huePanel.move(0, 56)
        self.newColorCard.setFixedSize(64, 164)
        self.newColorCard.move(376, 56)
        self.oldColorCard.setFixedSize(64, 164)
        self.oldColorCard.move(376, self.newColorCard.geometry().bottom() + 2)
        self.brightSlider.setFixedSize(330, 28)
        self.brightSlider.move(0, 408)

        self.editLabel.adjustSize()
        self.editLabel.move(0, 462)
        self.hexLineEdit.setFixedSize(230, 46)
        self.hexLineEdit.move(280, 458)
        self.hexLineEdit.setTextMargins(4, 0, 42, 0)
        if getattr(self.hexLineEdit, "prefixLabel", None) is not None:
            self.hexLineEdit.prefixLabel.adjustSize()
            self.hexLineEdit.prefixLabel.move(8, 10)

        for index, (edit, label) in enumerate(
            (
                (self.redLineEdit, self.redLabel),
                (self.greenLineEdit, self.greenLabel),
                (self.blueLineEdit, self.blueLabel),
            )
        ):
            edit.setFixedSize(190, 46)
            edit.move(0, 518 + index * 56)
            label.adjustSize()
            label.move(210, 518 + index * 56 + 10)

        bottom = self.blueLineEdit.geometry().bottom()
        if self.enableAlpha:
            self.opacityLineEdit.setFixedSize(190, 46)
            self.opacityLineEdit.move(0, bottom + 12)
            self.opacityLabel.adjustSize()
            self.opacityLabel.move(210, bottom + 22)
            if getattr(self.opacityLineEdit, "suffixLabel", None) is not None:
                self.opacityLineEdit.suffixLabel.move(
                    self.opacityLineEdit.fontMetrics().boundingRect(self.opacityLineEdit.text()).width() + 18, 10
                )
            self.scrollWidget.resize(560, 780)
            widget.resize(600, 936)

        self.buttonGroup.setFixedSize(598, 96)
        self.yesButton.setFixedSize(266, 48)
        self.yesButton.move(26, 24)
        self.cancelButton.setFixedSize(266, 48)
        self.cancelButton.move(306, 24)
class InfoToolTipFilter(ToolTipFilter):
    """ToolTipFilter whose bubble text is larger than the 12px qfluentwidgets default."""

    def _createToolTip(self):
        tip = super()._createToolTip()
        tip.label.setStyleSheet(
            f"{FONT_STACK_QSS} font-size: {SETTING_TIP_FONT_PX}px; color: rgb(24, 32, 40);"
            " background: transparent; border: none;"
        )
        tip.label.adjustSize()
        tip.adjustSize()
        return tip


class FluentSettingRow(CardWidget):
    def __init__(self, title: str, content: str, control: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(94)
        self.setObjectName("fluentSettingRow")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(22)

        text_layout = QHBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(6)

        title_label = BodyLabel(title)
        set_label_font(title_label, SETTING_ROW_TITLE_FONT_PX)
        text_layout.addWidget(title_label)
        if content:
            info = TransparentToolButton(FluentIcon.INFO, self)
            info.setFixedSize(32, 32)
            info.setIconSize(QSize(20, 20))
            info.setCursor(Qt.WhatsThisCursor)
            info.setToolTip(content)
            info.installEventFilter(InfoToolTipFilter(info, showDelay=200, position=ToolTipPosition.TOP))
            text_layout.addWidget(info, 0, Qt.AlignVCenter)
        text_layout.addStretch()
        layout.addLayout(text_layout, 1)
        layout.addWidget(control, 0, Qt.AlignVCenter)
class FramelessDragMixin:
    """Click-drag a frameless dialog by any spot no child widget consumes (header, gaps).

    Non-interactive children (labels, frames) ignore mouse presses, so the press
    propagates up to the dialog; interactive controls keep working untouched.
    """

    _drag_offset: QPoint | None = None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)
