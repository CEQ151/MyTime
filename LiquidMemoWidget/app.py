from __future__ import annotations

import math
import os
import re
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

sys.dont_write_bytecode = True
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

from PySide6.QtCore import (
    QEvent,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    QThreadPool,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QCursor, QFontMetrics, QPainter, QPixmap, QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    Action,
    BodyLabel,
    CardWidget,
    FluentIcon,
    MenuAnimationType,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    SmoothScrollArea,
    TitleLabel,
    ToolTipPosition,
    setTheme,
    Theme,
)

from skin_editor import load_skin_pixmap, mean_luminance as image_mean_luminance
from state_store import (
    CalendarEvent,
    Settings,
    StateStore,
    SubTask,
    TodoItem,
    deadline_alert,
    parse_ddl,
    utc_now,
)
from recurrence import (
    RECUR_CHOICES,
    apply_recurrence_on_complete,
    next_occurrence,
    parse_recur,
    recur_label,
)
from wheel_hook import GlobalWheelHook
from window_layer import (
    HTBOTTOM,
    HTBOTTOMLEFT,
    HTBOTTOMRIGHT,
    HTCAPTION,
    HTCLIENT,
    HTLEFT,
    HTRIGHT,
    HTTRANSPARENT,
    WM_ENTERSIZEMOVE,
    WM_EXITSIZEMOVE,
    WM_NCHITTEST,
    apply_tool_window,
    begin_system_move,
    detach_from_parent,
    set_capture_exclusion,
    set_rounded_corners,
    set_topmost,
    protect_window_from_capture,
)
from qframelesswindow.windows.window_effect import WindowsWindowEffect
from ui_common import (
    FONT_STACK_QSS,
    InfoToolTipFilter,
    POPUP_INPUT_FONT_PX,
    SETTING_CONTROL_FONT_PX,
    SETTING_ROW_TITLE_FONT_PX,
    SETTING_STATUS_FONT_PX,
    SETTING_TITLE_FONT_PX,
    best_contrast_color,
    css_rgba,
    enlarge_control_font,
    mixed_font,
    mixed_font_px,
    qcolor,
    relative_luminance,
    scaled_dialog_size,
    set_label_font,
    tray_icon,
)
from settings_ui import SettingsWindow
from update_ui import UpdateManager
from calendar_manager import CalendarManager
from notify_manager import NotificationManager
from startup import reconcile_startup
from floating_launcher import FloatingModeController
from ink_theme import InkTheme, InkThemeController, _darken
from ink_swirl import swirl_tokens
from ink_background import make_ink_background


# MIN_WIDTH is derived below, once row_fixed_chrome and the DDL column constants exist —
# the old fixed 320 let a narrow window clip the trailing ❗ / ✎ buttons.
MAX_WIDTH_RATIO = 0.52
MIN_HEIGHT = 320
MAX_HEIGHT_RATIO = 0.7
# 灵动水墨 is an airy ink-wash surface; give its text a little more room than the frost skin.
INK_MIN_WIDTH = 400
# Native drag-resize hot zones along the bottom corners ("左右下角"): an RESIZE_EDGE-thick strip
# along the bottom/left/right edges, widening to RESIZE_CORNER squares at the two bottom corners.
# They sit in the otherwise click-through frame, so they must stay clear of the content inset
# (OUTER_X = 26 / corner_margin = 14 exceed all of these).
RESIZE_EDGE = 8
RESIZE_CORNER = 20
RESIZE_SIDE = 28
# Forgiveness margin around a todo row's checkbox: inside it the cursor promises a click
# (pointing hand), a press never arms drag-reorder, and release toggles the box.
CHECKBOX_ZONE_PAD = 6
# A vertical scrollbar lives inside the list viewport. Reserve its full painted width in the
# collapsed layout even before Qt decides whether it is needed; otherwise adding the calendar
# header can make the bar appear and steal pixels from already-fixed DDL/calendar columns.
SCROLLBAR_LAYOUT_RESERVE = 12
ROW_HEIGHT = 44
OUTER_X = 26
# 空状态占位文字（「暂无待办」）；kept in one constant so both construction and recolour paths stay in sync.
EMPTY_HINT_FONT_PX = 20
# DDL column: a fixed-width deadline column shown to the right of each todo's text,
# separated from it by a solid vertical line. Width is only reserved from the text
# column when at least one active todo actually carries a ddl.
# DDL column width is adaptive: sized to fit the widest deadline text in the current view so
# dates always show in full, clamped to [MIN, MAX] so a single long string can't blow up the
# window (anything past MAX still elides).
DDL_COL_MIN = 64
DDL_COL_MAX = 240
DDL_COL_EXPANDED_MAX = 600  # in expanded mode the time column may grow to avoid any elision
DDL_COL_PAD = 6
DDL_SEP_WIDTH = 1
# Two extra HBox gaps (text↔separator and separator↔ddl) at the layout's 10px spacing.
DDL_COL_GAPS = 20

ROW_MIN_TEXT = 90  # the title column never shrinks below this; it elides past it


def row_fixed_chrome(ddl_reserve: int = 0, has_subtasks: bool = False) -> int:
    """Horizontal pixels a TodoRow needs besides its (elided) title. One authoritative count:
    window side padding (OUTER_X*2), the row's own margins (6*2) and 5 inter-item spacings
    (10), checkbox 24, edit ✎ 30, urgent ❗ 30, the DDL column reserve, and — when the todo
    has subtasks — the ▾ toggle (20) plus a spacing and the n/m badge (~34) plus a spacing.
    The old hand-counted reserves in _adaptive_width/_text_width_for_window disagreed with
    this by ~70px, which pushed the trailing ❗ out of the window at small widths."""
    base = OUTER_X * 2 + 12 + 50 + 24 + 30 + 30 + ddl_reserve
    return base + (74 if has_subtasks else 0)


# The narrowest window must still show every part of the widest row shape (DDL column shown,
# subtask toggle + badge present) with at least ROW_MIN_TEXT of title: otherwise some window
# size could always clip the trailing ❗ / ✎ buttons.
MIN_WIDTH = row_fixed_chrome(DDL_COL_MIN + DDL_SEP_WIDTH + DDL_COL_GAPS, True) + ROW_MIN_TEXT
MAX_WIDTH = 720
# Deadline highlighting: a parsed DDL already past "now" turns red; one due within the
# user-configured nearHighlightDays turns amber. Unparseable or done items stay normal.
DDL_OVERDUE_COLOR = "#FF3B30"
DDL_NEAR_COLOR = "#FF9500"
# Placeholder shown in an empty (but visible) DDL cell, signalling it is click-to-set.
DDL_EMPTY_HINT = "＋"
# Calendar subscription ("日程" group).
CALENDAR_HEADER_HEIGHT = 30
_WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]
# Top/bottom breathing room inside the scrollable list, so the first and last rows are never
# flush against the scroll viewport edge (near the window's rounded top/bottom) and get visually
# clipped. Counted in the window-height budget so it never squeezes.
LIST_EDGE_PAD = 7


def format_event_time(event: "CalendarEvent") -> str:
    """Compact local time shown in the calendar event's time column."""
    deadline = parse_ddl(event.start)
    if deadline is None:
        return event.start
    weekday = _WEEKDAY_CN[deadline.weekday()]
    if event.allDay:
        return f"{deadline.strftime('%m-%d')} 周{weekday} 全天"
    return f"{deadline.strftime('%m-%d')} 周{weekday} {deadline.strftime('%H:%M')}"

# Edge auto-hide ("dock"): when the window is dragged within DOCK_THRESHOLD px of a work-area
# edge (left/right/top) it snaps flush and, once the cursor leaves, slides off-screen leaving a
# DOCK_PEEK-px strip. Moving the cursor back onto that strip slides it out again.
WM_MOUSEMOVE = 0x0200
DOCK_THRESHOLD = 18
DOCK_PEEK = 5
DOCK_HIDE_DELAY_MS = 600
DOCK_SLIDE_MS = 200
DOCK_POLL_MS = 120


def install_tooltip(widget: QWidget) -> None:
    """Show the widget's tooltip as a readable qfluentwidgets bubble instead of a native
    QToolTip. A native tooltip here inherits the owning row/button's text color (white on a
    dark desktop) and an app-level `QToolTip` QSS rule cannot reliably override it, so the
    bubble renders unreadable (black-on-black / white-on-white). The bubble sets its own
    dark text + light background and reads the widget's existing setToolTip() text."""
    widget.installEventFilter(InfoToolTipFilter(widget, showDelay=400, position=ToolTipPosition.TOP))


def location_line_height() -> int:
    """Extra height a row gains from its dim second '📍 location' line (the 10pt label plus the
    2px text-column spacing). Shared by row layout and the window height pre-calc so they agree."""
    return QFontMetrics(mixed_font(10)).height() + 2


def subtask_line_height() -> int:
    """Height one indented subtask row adds to its parent row (small check + 10pt label + gap)."""
    return QFontMetrics(mixed_font(10)).height() + 5


SUBTASK_INDENT = 30
# Small round ordering/delete buttons inside the editor's subtask rows.
SUBTASK_BTN_SIZE = 24


def recur_hint_text(todo: "TodoItem") -> str:
    """Tooltip for a row's 🔄 recurrence marker: label, last completion, next occurrence."""
    parts = [recur_label(todo.recur)]
    if todo.lastDoneAt:
        try:
            parts.append("上次完成 " + datetime.fromisoformat(todo.lastDoneAt).strftime("%m-%d %H:%M"))
        except ValueError:
            pass
    ddl_dt = parse_ddl(todo.ddl or "")
    now = datetime.now()
    if ddl_dt is not None and ddl_dt < now:
        nxt = next_occurrence(todo.recur, ddl_dt, todo.recurAnchor, now)
        if nxt is not None:
            parts.append(f"完成后将排到 {nxt.strftime('%m-%d %H:%M')}")
    return " · ".join(parts)


def todo_extra_height(todo: "TodoItem") -> int:
    """Vertical space a todo's subtask block and recurrence line add below its title/location,
    mirrored by TodoRow.apply_text_width and the window height pre-calc."""
    extra = 0
    if todo.subtasks and not todo.subtasksCollapsed:
        extra += len(todo.subtasks) * subtask_line_height() + 2
    if parse_recur(todo.recur) not in (None, "none"):
        extra += location_line_height()
    return extra


def details_rich_text(text: str) -> str:
    """Markdown details -> inline-styled HTML for QLabel rich text (same pipeline spirit as
    update_ui.add_notes: QTextDocument markdown export with our typography injected)."""
    # Only # / ## headings are supported: deeper levels are demoted to bold lines so the
    # popup's hierarchy stays two levels tall.
    lines = []
    for line in (text or "").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("###"):
            line = line[: len(line) - len(stripped)] + "**" + stripped.lstrip("#").strip() + "**"
        lines.append(line)
    doc = QTextDocument()
    doc.setMarkdown("\n".join(lines).strip() or "_（无细节）_")
    rich = doc.toHtml()
    # pt, not px: px in rich text is a device pixel and ignores Windows DPI scaling, so on a
    # scaled display the popup text rendered smaller than the (pt-sized) row text no matter
    # the number. pt tracks the same scaling as every other widget font in the app.
    body_style = " font-family:'Times New Roman','Microsoft YaHei','Segoe UI Emoji'; font-size:13pt; font-weight:400;"
    rich = re.sub(r'<body style="[^"]*"', f'<body style="{body_style}"', rich)
    # QTextDocument exports xx-large heading sizes that dwarf the 13pt body; pin both levels.
    rich = re.sub(r'(<h1 style="[^"]*?)font-size:[^;\"]*', r"\1font-size:17pt", rich)
    rich = re.sub(r'(<h2 style="[^"]*?)font-size:[^;\"]*', r"\1font-size:15pt", rich)
    return rich


def details_html_height(html: str, width: int, font: QFont) -> int:
    """Measured wrapped height of the details HTML at ``width``. QLabel.heightForWidth
    returns -1 for rich text, and its sizeHint is the unwrapped one-line height — measuring
    through a QTextDocument is the only reliable number for the hover popup."""
    doc = QTextDocument()
    doc.setDefaultFont(font)
    doc.setHtml(html)
    doc.setTextWidth(max(60, width))
    return round(doc.size().height())


# 18px check indicator shared by a row's subtask lines (and the preview popup's list).
SUB_CHECK_QSS = """    QCheckBox::indicator {
        width: 18px; height: 18px; border-radius: 5px;
        border: 1px solid rgba(25,35,45,120); background: rgba(255,255,255,90);
    }
    QCheckBox::indicator:hover { background: rgba(255,255,255,150); }
    QCheckBox::indicator:checked { background: #111820; image: none; }
"""


class SubTaskLabel(QLabel):
    """One subtask's text label. Accepts its own mouse events so a press on the checklist never
    reaches the parent TodoRow's drag-reorder path; a plain left click toggles `check_target`."""

    def __init__(self, text: str, check_target: QCheckBox | None = None) -> None:
        super().__init__(text)
        self.check_target = check_target
        self.setTextFormat(Qt.PlainText)

    def mousePressEvent(self, event) -> None:
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.check_target is not None:
            self.check_target.click()
        event.accept()


_text_measure_label: QLabel | None = None


def wrapped_text_height(text: str, width: int) -> int:
    """Height a 12pt word-wrapped title label needs to show `text` in full at `width`.

    Measured through an off-screen QLabel configured exactly like the real title labels
    (TodoTextLabel: PlainText, wordWrap), so the value matches what the label actually
    renders. QFontMetrics.boundingRect with TextWrapAnywhere disagreed with QLabel's
    word-boundary wrapping and underestimated tall rows, clipping the first/last line."""
    global _text_measure_label
    label = _text_measure_label
    if label is None:
        label = QLabel()
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(True)
        label.setFont(mixed_font(12))
        _text_measure_label = label
    label.setText(text)
    height = label.heightForWidth(max(90, width))
    if height <= 0:  # QLabel returns -1 if it somehow has no height-for-width
        height = QFontMetrics(mixed_font(12)).boundingRect(
            QRect(0, 0, max(90, width), 2000), Qt.TextWordWrap, text
        ).height()
    return height


class RoundButton(QPushButton):
    def __init__(self, text: str, size: int = 34, parent: QWidget | None = None, tone: str = "neutral") -> None:
        super().__init__(text, parent)
        self.tone = tone
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.apply_ink_theme(None)
        install_tooltip(self)

    def apply_ink_theme(self, theme: "InkTheme | None") -> None:
        palette = {
            "neutral": ("rgba(255,255,255,88)", "rgba(255,255,255,132)", "rgba(255,255,255,175)", "#111820", "rgba(255,255,255,150)"),
            "add": ("rgba(33,150,243,196)", "rgba(33,150,243,225)", "rgba(18,121,218,235)", "white", "rgba(255,255,255,170)"),
            "hide": ("rgba(255,255,255,105)", "rgba(255,255,255,150)", "rgba(255,255,255,190)", "#30404C", "rgba(255,255,255,150)"),
            "confirm": ("rgba(45,184,130,205)", "rgba(45,184,130,235)", "rgba(24,146,101,242)", "white", "rgba(255,255,255,170)"),
        }
        if theme is not None:
            accent, pressed = theme.accent, _darken(theme.accent, 0.13)
            palette["add"] = (
                css_rgba(qcolor(accent), 0.82), css_rgba(qcolor(accent), 0.92), css_rgba(qcolor(pressed), 0.95),
                "white", "rgba(255,255,255,190)",
            )
            palette["confirm"] = palette["add"]
        bg, hover, pressed, color, border = palette.get(self.tone, palette["neutral"])
        radius = self.width() // 2
        self.setStyleSheet(
            f"""
            QPushButton {{
                {FONT_STACK_QSS}
                border: 1px solid {border};
                border-radius: {radius}px;
                background: {bg};
                color: {color};
                font-size: 17px;
                font-weight: 700;
            }}
            QPushButton:hover {{ background: {hover}; }}
            QPushButton:pressed {{ background: {pressed}; }}
            """
        )


class TodoTextLabel(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setToolTip(text)
        install_tooltip(self)
        self.setTextFormat(Qt.PlainText)
        self.setWordWrap(True)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def set_full_text(self, text: str) -> None:
        self.setToolTip(text)
        self.setText(text)


class DDLCell(TodoTextLabel):
    """Deadline column label. Emits `clicked` so its row can open the DDL editor; the row also
    registers it with the native hit-test so the click lands here instead of passing through."""

    clicked = Signal()

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setWordWrap(False)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class DragHandle(QLabel):
    def __init__(self, parent_window: "MemoWindow") -> None:
        super().__init__("⋮⋮", parent_window.content)
        self.parent_window = parent_window
        self.setFixedSize(38, 32)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.SizeAllCursor)
        self.setStyleSheet(
            f"""
            QLabel {{
                {FONT_STACK_QSS}
                color: rgba(17,24,32,185);
                font-size: 20px;
                border-radius: 16px;
                background: rgba(255,255,255,96);
            }}
            QLabel:hover {{ background: rgba(255,255,255,145); }}
            """
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.parent_window.begin_system_move()
            event.accept()
            return
        super().mousePressEvent(event)


class TodoRow(QFrame):
    def __init__(self, todo: TodoItem, settings: Settings, parent_window: "MemoWindow") -> None:
        super().__init__(parent_window.content)
        self.todo = todo
        self.settings = settings
        self.parent_window = parent_window
        self._drag_start: QPoint | None = None
        self._dragging = False
        self._zone_press = False
        self._style_signature: tuple[str, bool, bool, str] | None = None
        self._halo: QGraphicsDropShadowEffect | None = None
        self._preview_timer: QTimer | None = None
        self._flash_active = False
        self._sub_labels: list[QLabel] = []
        self.setMinimumHeight(ROW_HEIGHT)
        self.setObjectName("todoRow")
        self.setCursor(Qt.OpenHandCursor)
        # Hover moves (no buttons held) reach mouseMoveEvent so the cursor can flip between the
        # drag hand and the checkbox click pointer as the pointer travels along the row.
        self.setMouseTracking(True)
        self.setStyleSheet(
            f"""
            QFrame#todoRow {{
                {FONT_STACK_QSS}
                background: transparent;
                border-bottom: 1px solid rgba(255,255,255,72);
            }}
            QFrame#todoRow:hover {{ background: rgba(255,255,255,35); }}
            QFrame#todoRow[flash="true"] {{ background: rgba(45,184,130,80); }}
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(10)

        self.checkbox = QCheckBox()
        self.checkbox.setCursor(Qt.PointingHandCursor)
        self.checkbox.setChecked(todo.done)
        self.checkbox.setStyleSheet(
            """
            QCheckBox::indicator {
                width: 24px;
                height: 24px;
                border-radius: 7px;
                border: 1px solid rgba(25,35,45,120);
                background: rgba(255,255,255,80);
            }
            QCheckBox::indicator:hover { background: rgba(255,255,255,140); }
            QCheckBox::indicator:checked {
                background: #111820;
                image: none;
            }
            """
        )
        self.checkbox.stateChanged.connect(self._complete_changed)
        layout.addWidget(self.checkbox)

        # 子任务折叠开关 + 进度徽标: only present when the todo has subtasks. Collapsed rows show
        # an n/m (or ✓) count badge; expanded rows show the indented checklist under the title.
        self.sub_toggle = QPushButton("▾")
        self.sub_toggle.setFixedSize(20, 20)
        self.sub_toggle.setCursor(Qt.PointingHandCursor)
        self.sub_toggle.setToolTip("展开/收起子任务")
        self.sub_toggle.setStyleSheet(
            """
            QPushButton {
                border: none; border-radius: 10px; background: rgba(255,255,255,45);
                font-size: 11px; color: rgba(17,24,32,200); padding: 0;
            }
            QPushButton:hover { background: rgba(255,255,255,115); }
            """
        )
        self.sub_toggle.clicked.connect(self._toggle_collapsed)
        self.sub_toggle.setVisible(False)
        layout.addWidget(self.sub_toggle)

        self.sub_badge = QLabel("")
        self.sub_badge.setFont(mixed_font(10))
        self.sub_badge.setVisible(False)
        layout.addWidget(self.sub_badge)

        self.text = TodoTextLabel(todo.text)
        self.text.setFont(mixed_font(12))
        self.text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        # Let a press on the title reach the row so the whole non-control surface can drag.
        self.text.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        # Location shows on a dim second line under the title, only when set (📍 …); the column
        # stays single-line otherwise so rows without a location are not taller.
        self.location_label = TodoTextLabel("")
        self.location_label.setFont(mixed_font(10))
        self.location_label.setWordWrap(False)
        self.location_label.setVisible(False)
        self.location_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        text_col.addWidget(self.text)
        text_col.addWidget(self.location_label)
        # 🔄 recurrence marker line (label + last completion + next-occurrence hint in tooltip).
        self.recur_label = TodoTextLabel("")
        self.recur_label.setFont(mixed_font(10))
        self.recur_label.setWordWrap(False)
        self.recur_label.setVisible(False)
        self.recur_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        text_col.addWidget(self.recur_label)
        # Indented subtask checklist (hidden when collapsed or when there are no subtasks).
        self.subtask_area = QWidget()
        self.subtask_area.setStyleSheet("background: transparent;")
        self.subtask_col = QVBoxLayout(self.subtask_area)
        self.subtask_col.setContentsMargins(SUBTASK_INDENT, 2, 0, 0)
        self.subtask_col.setSpacing(3)
        self.subtask_area.setVisible(False)
        text_col.addWidget(self.subtask_area)
        layout.addLayout(text_col, 1)

        if parse_recur(todo.recur) not in (None, "none"):
            self.recur_label.set_full_text(f"🔄 {recur_label(todo.recur)}")
            self.recur_label.setVisible(True)
            self.recur_label.setToolTip(recur_hint_text(todo))
            install_tooltip(self.recur_label)
        self._rebuild_subtask_rows()

        # DDL column: solid vertical separator + deadline label. Both stay hidden until the
        # layout pass (apply_text_width) decides the column should be shown for this view.
        self.ddl_sep = QFrame()
        self.ddl_sep.setObjectName("ddlSeparator")
        self.ddl_sep.setFixedWidth(DDL_SEP_WIDTH)
        self.ddl_sep.setStyleSheet("QFrame#ddlSeparator { background: rgba(25,35,45,110); border: none; }")
        self.ddl_sep.setVisible(False)
        layout.addWidget(self.ddl_sep)

        self.ddl_label = DDLCell(todo.ddl)
        self.ddl_label.setFont(mixed_font(11))
        self.ddl_label.setFixedWidth(DDL_COL_MIN)  # adaptive width set in apply_text_width
        self.ddl_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.ddl_label.setToolTip(todo.ddl or "点击编辑事项")
        self.ddl_label.setVisible(False)
        self.ddl_label.clicked.connect(lambda: parent_window.edit_todo(todo.id))
        layout.addWidget(self.ddl_label)

        # Style text + ddl together, now that both labels exist.
        self.apply_text_style(parent_window.text_color_for(todo), parent_window.text_needs_halo())

        self.edit_btn = QPushButton("✎")
        self.edit_btn.setFixedSize(30, 30)
        self.edit_btn.setCursor(Qt.PointingHandCursor)
        self.edit_btn.setToolTip("编辑事项（内容 / 地点 / DDL）")
        install_tooltip(self.edit_btn)
        self.edit_btn.setStyleSheet(
            """
            QPushButton {
                border: none;
                border-radius: 15px;
                background: rgba(255,255,255,45);
                font-size: 14px;
            }
            QPushButton:hover { background: rgba(255,255,255,115); }
            QPushButton:pressed { background: rgba(255,255,255,160); }
            """
        )
        self.edit_btn.clicked.connect(lambda: parent_window.edit_todo(todo.id))
        layout.addWidget(self.edit_btn)

        self.urgent = QPushButton("❗")
        self.urgent.setFixedSize(30, 30)
        self.urgent.setCursor(Qt.PointingHandCursor)
        self.urgent.setToolTip("加急并置顶")
        install_tooltip(self.urgent)
        self.urgent.setStyleSheet(
            """
            QPushButton {
                border: none;
                border-radius: 15px;
                background: rgba(255,255,255,45);
                font-size: 15px;
            }
            QPushButton:hover { background: rgba(255,255,255,115); }
            QPushButton:pressed { background: rgba(255,255,255,160); }
            """
        )
        self.urgent.clicked.connect(lambda: parent_window.toggle_urgent(todo.id))
        layout.addWidget(self.urgent)

    def _ddl_status(self) -> str:
        # "overdue"/"near"/"normal"/"none" — drives the deadline color. Done items and
        # cells whose text we cannot parse into a date never get the alert colors.
        if self.todo.done or not self.todo.ddl.strip():
            return "none"
        return deadline_alert(self.todo.ddl, self.settings.nearHighlightDays)

    def apply_text_style(self, color: QColor, protect: bool) -> None:
        # Re-applying an identical style (and especially swapping in a brand-new
        # QGraphicsDropShadowEffect) forces a repaint of the row; with the contrast timer
        # firing every few hundred ms that reads as text flicker. Skip no-op updates and
        # reuse the existing halo effect.
        ddl_status = self._ddl_status()
        signature = (color.name(), self.todo.done, protect, ddl_status)
        if signature == self._style_signature:
            return
        self._style_signature = signature
        alpha = 0.45 if self.todo.done else 1.0
        decoration = "text-decoration: line-through;" if self.todo.done else ""
        self.text.setStyleSheet(f"{FONT_STACK_QSS} font-size: 12pt; color: {css_rgba(color, alpha)}; {decoration}")
        self.location_label.setStyleSheet(f"{FONT_STACK_QSS} font-size: 10pt; color: {css_rgba(color, alpha * 0.6)}; {decoration}")
        if ddl_status == "overdue":
            ddl_css = f"color: {DDL_OVERDUE_COLOR}; font-weight: 600;"
        elif ddl_status == "near":
            ddl_css = f"color: {DDL_NEAR_COLOR}; font-weight: 600;"
        elif self.todo.ddl.strip():
            ddl_css = f"color: {css_rgba(color, alpha * 0.85)};"
        else:
            ddl_css = f"color: {css_rgba(color, alpha * 0.4)};"  # faint click-to-set hint
        self.ddl_label.setStyleSheet(f"{FONT_STACK_QSS} font-size: 11pt; {ddl_css} {decoration}")
        if protect:
            halo = self._halo
            if halo is None:
                halo = QGraphicsDropShadowEffect(self.text)
                halo.setBlurRadius(3.2)
                halo.setOffset(0, 0)
                self._halo = halo
                self.text.setGraphicsEffect(halo)
            if relative_luminance(color) > 0.55:
                halo.setColor(QColor(0, 0, 0, 118))
            else:
                halo.setColor(QColor(255, 255, 255, 138))
        elif self._halo is not None:
            self._halo = None
            self.text.setGraphicsEffect(None)

    def apply_text_width(self, text_width: int, show_ddl: bool = False, ddl_width: int = DDL_COL_MIN) -> int:
        text_width = max(90, text_width)
        self.text.setFixedWidth(text_width)
        self.ddl_sep.setVisible(show_ddl)
        self.ddl_label.setVisible(show_ddl)
        if show_ddl:
            self.ddl_label.setFixedWidth(ddl_width)
            raw = self.todo.ddl.strip()
            if raw:
                ddl_metrics = QFontMetrics(self.ddl_label.font())
                self.ddl_label.setText(ddl_metrics.elidedText(raw, Qt.ElideRight, ddl_width))
            else:
                self.ddl_label.setText(DDL_EMPTY_HINT)
        loc = self.todo.location.strip()
        if loc:
            self.location_label.setFixedWidth(text_width)
            loc_metrics = QFontMetrics(self.location_label.font())
            self.location_label.set_full_text(f"📍 {loc}")
            self.location_label.setText(loc_metrics.elidedText(f"📍 {loc}", Qt.ElideRight, text_width))
        self.location_label.setVisible(bool(loc))
        if self.recur_label.isVisible():
            self.recur_label.setFixedWidth(text_width)
            metrics10 = QFontMetrics(self.recur_label.font())
            full = f"🔄 {recur_label(self.todo.recur)}"
            self.recur_label.set_full_text(full)
            self.recur_label.setText(metrics10.elidedText(full, Qt.ElideRight, text_width))
        sub_width = max(60, text_width - SUBTASK_INDENT)
        sub_metrics = QFontMetrics(mixed_font(10))
        for label, sub in zip(self._sub_labels, self.todo.subtasks):
            label.setFixedWidth(sub_width)
            label.setText(sub_metrics.elidedText(sub.text, Qt.ElideRight, sub_width))
        height = wrapped_text_height(self.text.text(), text_width) + (location_line_height() if loc else 0)
        height += todo_extra_height(self.todo)
        height = max(ROW_HEIGHT, height + 18)
        self.setFixedHeight(height)
        return height

    def _complete_changed(self) -> None:
        self.parent_window.complete_todo(self.todo.id, self.checkbox.isChecked(), self)

    # ── Subtask checklist rendering ──────────────────────────────────────────────────────
    def _subtasks_visible(self) -> bool:
        return bool(self.todo.subtasks) and not self.todo.subtasksCollapsed

    def _rebuild_subtask_rows(self) -> None:
        """Rebuild the indented subtask checklist from todo.subtasks and sync the toggle/badge."""
        while self.subtask_col.count():
            item = self.subtask_col.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._sub_labels = []
        color = "rgba(17,24,32,175)"
        for sub in self.todo.subtasks:
            row = QWidget(self.subtask_area)
            row.setStyleSheet("background: transparent;")
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            check = QCheckBox()
            check.setChecked(sub.done)
            check.setStyleSheet(SUB_CHECK_QSS)
            check.setCursor(Qt.PointingHandCursor)
            check.setToolTip(sub.text)
            check.toggled.connect(
                lambda checked, sub_id=sub.id: self.parent_window.complete_subtask(self.todo.id, sub_id, checked)
            )
            h.addWidget(check)
            label = SubTaskLabel("", check)
            label.setFont(mixed_font(10))
            label.setStyleSheet(f"{FONT_STACK_QSS} font-size: 10pt; color: {color};")
            label.setCursor(Qt.PointingHandCursor)
            self._sub_labels.append(label)
            h.addWidget(label, 1)
            self.subtask_col.addWidget(row)
        has_subs = bool(self.todo.subtasks)
        self.subtask_area.setVisible(self._subtasks_visible())
        self.sub_toggle.setVisible(has_subs)
        self.sub_toggle.setText("▾" if self._subtasks_visible() else "▸")
        if has_subs and self.todo.subtasksCollapsed:
            done = sum(1 for sub in self.todo.subtasks if sub.done)
            total = len(self.todo.subtasks)
            self.sub_badge.setText("✓" if done == total else f"{done}/{total}")
            self.sub_badge.setVisible(True)
        elif not self._flash_active:
            self.sub_badge.setVisible(False)

    def refresh_subtask_state(self) -> None:
        """Targeted in-place update (no row rebuild): re-check boxes/badge against todo state."""
        checks = self.subtask_area.findChildren(QCheckBox)
        for check, sub in zip(checks, self.todo.subtasks):
            check.blockSignals(True)
            check.setChecked(sub.done)
            check.blockSignals(False)
        if self.todo.subtasks and self.todo.subtasksCollapsed:
            done = sum(1 for sub in self.todo.subtasks if sub.done)
            total = len(self.todo.subtasks)
            self.sub_badge.setText("✓" if done == total else f"{done}/{total}")
            self.sub_badge.setVisible(True)
        elif not self._flash_active:
            self.sub_badge.setVisible(False)

    def _toggle_collapsed(self) -> None:
        toggle = getattr(self.parent_window, "toggle_subtasks_collapsed", None)
        if callable(toggle):
            toggle(self.todo.id)

    def flash_completion(self) -> None:
        """Brief green highlight + transient hint when the last open subtask was checked."""
        if self._flash_active:
            return
        self._flash_active = True
        self.sub_badge.setText("✓ 已完成")
        self.sub_badge.setVisible(True)
        self.setProperty("flash", True)
        self._repolish()
        # Child timer: dies with the row, so a pending flash can never fire on a deleted row.
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_flash)
        self._flash_timer.start(1200)

    def _end_flash(self) -> None:
        self._flash_active = False
        self.setProperty("flash", False)
        self._repolish()
        self.refresh_subtask_state()

    def _repolish(self) -> None:
        style = self.style()
        style.unpolish(self)
        style.polish(self)

    # ── Hover details preview (DetailsPopup) ─────────────────────────────────────────────
    def _cancel_preview(self) -> None:
        if self._preview_timer is not None:
            self._preview_timer.stop()
            self._preview_timer = None

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        # Returning to the row while the preview is up (within its close grace period) keeps
        # it open instead of blinking out and re-arming the 1.0s timer.
        # The popup lives on the MemoWindow, not the row — reaching through parent_window here
        # (an AttributeError on self.details_popup would kill the whole hover chain silently).
        popup = getattr(self.parent_window, "details_popup", None)
        if popup is not None and popup.isVisible():
            popup._cancel_close()
        if not (self.todo.details or self.todo.subtasks) or self.todo.done:
            return
        show = getattr(self.parent_window, "show_details_preview", None)
        if not callable(show):
            return
        self._cancel_preview()
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(lambda: show(self))
        self._preview_timer.start(1000)

    def leaveEvent(self, event) -> None:
        self._cancel_preview()
        hide = getattr(self.parent_window, "hide_details_preview", None)
        if callable(hide):
            hide()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        self._cancel_preview()
        hide = getattr(self.parent_window, "hide_details_preview", None)
        if callable(hide):
            hide()
        super().hideEvent(event)

    def _in_subtask_zone(self, pos: QPoint) -> bool:
        """True when `pos` is over the indented subtask checklist block; presses there must not
        arm the parent row's drag-reorder."""
        if not self.subtask_area.isVisibleTo(self):
            return False
        top_left = self.subtask_area.mapTo(self, QPoint(0, 0))
        zone = QRect(top_left, self.subtask_area.size())
        return zone.adjusted(-2, -2, 2, 2).contains(pos)

    def _in_checkbox_zone(self, pos: QPoint) -> bool:
        zone = self.checkbox.geometry().adjusted(
            -CHECKBOX_ZONE_PAD, -CHECKBOX_ZONE_PAD, CHECKBOX_ZONE_PAD, CHECKBOX_ZONE_PAD
        )
        return zone.intersected(self.rect()).contains(pos)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            if self._in_checkbox_zone(event.position().toPoint()):
                # Inside the checkbox's forgiveness margin: never arm the reorder drag, or a
                # click aimed at the box tilts into a row drag before the threshold is crossed.
                self._zone_press = True
                self._cancel_preview()
                event.accept()
                return
            if self._in_subtask_zone(event.position().toPoint()):
                # Presses over the subtask checklist must never drag-reorder the parent row.
                self._zone_press = False
                self._drag_start = None
                self._cancel_preview()
                event.accept()
                return
            self._zone_press = False
            self._drag_start = event.position().toPoint()
            self._dragging = False
            # Accept the press so this row keeps Qt's implicit mouse grab while the pointer moves
            # across sibling rows; its move/release handlers can then drive the full drag session.
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_start is None or not (event.buttons() & Qt.LeftButton):
            # Hover: inside the checkbox zone show the click pointer, elsewhere the drag hand, so
            # the cursor always tells the user what the next press will do before they commit.
            in_zone = self._in_checkbox_zone(event.position().toPoint())
            self.setCursor(Qt.PointingHandCursor if in_zone else Qt.OpenHandCursor)
            super().mouseMoveEvent(event)
            return
        distance = (event.position().toPoint() - self._drag_start).manhattanLength()
        if not self._dragging and distance >= QApplication.startDragDistance():
            self._dragging = True
            self.setCursor(Qt.ClosedHandCursor)
            self.parent_window.begin_todo_reorder(self)
        if self._dragging:
            self.parent_window.move_todo_reorder(self, event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        was_dragging = self._dragging
        zone_click = self._zone_press
        self._drag_start = None
        self._dragging = False
        self._zone_press = False
        self.setCursor(Qt.OpenHandCursor)
        if event.button() != Qt.LeftButton:
            super().mouseReleaseEvent(event)
            return
        if was_dragging:
            self.parent_window.finish_todo_reorder(self)
            event.accept()
            return
        if zone_click and self._in_checkbox_zone(event.position().toPoint()):
            # A press that started beside the box (the box itself is handled by the child widget
            # and never reaches here) honors the pointer cursor and toggles on release.
            self.checkbox.click()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class CalendarRow(QFrame):
    """A read-only synced calendar event. Mirrors TodoRow's apply_text_style / apply_text_width
    interface (and exposes .checkbox / .ddl_label) so the window's shared layout and contrast
    loops can treat it like a todo row. No urgent button; the time cell is display-only."""

    def __init__(self, event: CalendarEvent, done: bool, parent_window: "MemoWindow") -> None:
        super().__init__(parent_window.content)
        self.cal_event = event
        self.done = done
        self.parent_window = parent_window
        self._style_signature: tuple[str, bool, bool, str] | None = None
        self._halo: QGraphicsDropShadowEffect | None = None
        self.setMinimumHeight(ROW_HEIGHT)
        self.setObjectName("todoRow")
        self.setStyleSheet(
            f"""
            QFrame#todoRow {{
                {FONT_STACK_QSS}
                background: transparent;
                border-bottom: 1px solid rgba(255,255,255,72);
            }}
            QFrame#todoRow:hover {{ background: rgba(255,255,255,35); }}
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(10)

        self.checkbox = QCheckBox()
        self.checkbox.setCursor(Qt.PointingHandCursor)
        self.checkbox.setChecked(done)
        self.checkbox.setStyleSheet(
            """
            QCheckBox::indicator {
                width: 24px; height: 24px; border-radius: 7px;
                border: 1px solid rgba(25,35,45,120); background: rgba(255,255,255,80);
            }
            QCheckBox::indicator:hover { background: rgba(255,255,255,140); }
            QCheckBox::indicator:checked { background: #111820; image: none; }
            """
        )
        self.checkbox.stateChanged.connect(self._done_changed)
        layout.addWidget(self.checkbox)

        # A small glyph marks these rows as calendar events rather than user todos.
        self.text = TodoTextLabel(f"📅 {event.summary}")
        self.text.setFont(mixed_font(12))
        self.text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        # Location (from the ICS LOCATION field) on a dim second line, only when present.
        self.location_label = TodoTextLabel("")
        self.location_label.setFont(mixed_font(10))
        self.location_label.setWordWrap(False)
        self.location_label.setVisible(False)
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        text_col.addWidget(self.text)
        text_col.addWidget(self.location_label)
        layout.addLayout(text_col, 1)

        self.ddl_sep = QFrame()
        self.ddl_sep.setObjectName("ddlSeparator")
        self.ddl_sep.setFixedWidth(DDL_SEP_WIDTH)
        self.ddl_sep.setStyleSheet("QFrame#ddlSeparator { background: rgba(25,35,45,110); border: none; }")
        layout.addWidget(self.ddl_sep)

        self.ddl_label = TodoTextLabel(format_event_time(event))
        self.ddl_label.setFont(mixed_font(11))
        self.ddl_label.setWordWrap(False)
        self.ddl_label.setFixedWidth(DDL_COL_MIN)
        self.ddl_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self.ddl_label)

        self.apply_text_style(parent_window._normal_text_color(), parent_window.text_needs_halo())

    def _event_status(self) -> str:
        if self.done:
            return "none"
        return deadline_alert(self.cal_event.start, self.parent_window.app.state.settings.nearHighlightDays)

    def apply_text_style(self, color: QColor, protect: bool) -> None:
        status = self._event_status()
        signature = (color.name(), self.done, protect, status)
        if signature == self._style_signature:
            return
        self._style_signature = signature
        alpha = 0.4 if self.done else 1.0
        decoration = "text-decoration: line-through;" if self.done else ""
        self.text.setStyleSheet(f"{FONT_STACK_QSS} font-size: 12pt; color: {css_rgba(color, alpha)}; {decoration}")
        self.location_label.setStyleSheet(f"{FONT_STACK_QSS} font-size: 10pt; color: {css_rgba(color, alpha * 0.6)}; {decoration}")
        if status == "overdue":
            time_css = f"color: {DDL_OVERDUE_COLOR}; font-weight: 600;"
        elif status == "near":
            time_css = f"color: {DDL_NEAR_COLOR}; font-weight: 600;"
        else:
            time_css = f"color: {css_rgba(color, alpha * 0.85)};"
        self.ddl_label.setStyleSheet(f"{FONT_STACK_QSS} font-size: 11pt; {time_css} {decoration}")
        if protect:
            halo = self._halo
            if halo is None:
                halo = QGraphicsDropShadowEffect(self.text)
                halo.setBlurRadius(3.2)
                halo.setOffset(0, 0)
                self._halo = halo
                self.text.setGraphicsEffect(halo)
            halo.setColor(QColor(0, 0, 0, 118) if relative_luminance(color) > 0.55 else QColor(255, 255, 255, 138))
        elif self._halo is not None:
            self._halo = None
            self.text.setGraphicsEffect(None)

    def apply_text_width(self, text_width: int, show_ddl: bool = True, ddl_width: int = DDL_COL_MIN) -> int:
        text_width = max(90, text_width)
        self.text.setFixedWidth(text_width)
        self.ddl_label.setFixedWidth(ddl_width)
        metrics = QFontMetrics(self.ddl_label.font())
        self.ddl_label.setText(metrics.elidedText(format_event_time(self.cal_event), Qt.ElideRight, ddl_width))
        loc = self.cal_event.location.strip()
        if loc:
            self.location_label.setFixedWidth(text_width)
            loc_metrics = QFontMetrics(self.location_label.font())
            self.location_label.set_full_text(f"📍 {loc}")
            self.location_label.setText(loc_metrics.elidedText(f"📍 {loc}", Qt.ElideRight, text_width))
        self.location_label.setVisible(bool(loc))
        height = wrapped_text_height(self.text.text(), text_width) + (location_line_height() if loc else 0)
        height = max(ROW_HEIGHT, height + 18)
        self.setFixedHeight(height)
        return height

    def _done_changed(self) -> None:
        self.parent_window.toggle_calendar_event(self.cal_event.key, self.checkbox.isChecked())


class DetailsPopup(QDialog):
    """Frameless hover preview of a todo's details + subtasks, styled like the editor panel.
    Shown by TodoRow's 1.0s hover timer; any leave (row or popup) closes it immediately."""

    def __init__(self, parent_window: "MemoWindow") -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.parent_window = parent_window
        self._todo_id: str | None = None
        self._sub_checks: dict[str, QCheckBox] = {}
        self._close_timer: QTimer | None = None
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("事件细节")
        self.panel = QFrame(self)
        self.panel.setObjectName("detailsPanel")
        self.panel.setStyleSheet(self._panel_qss(None))
        outer = QVBoxLayout(self.panel)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(8)
        # Long markdown details overflow the 60%-screen height cap, so the content lives in a
        # transparent scroll area — the popup never clips text, tall ones just scroll.
        self.scroll = QScrollArea(self.panel)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # Slim but VISIBLE scrollbar: a hidden one gave no hint that long details continue
        # below the fold, so the popup looked like it silently truncated the markdown.
        self.scroll.setStyleSheet(
            """
            QScrollArea { background: transparent; }
            QScrollBar:vertical { background: transparent; width: 8px; margin: 4px 2px 4px 0; }
            QScrollBar::handle:vertical { background: rgba(17,24,32,70); border-radius: 4px; min-height: 30px; }
            QScrollBar::handle:vertical:hover { background: rgba(17,24,32,120); }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
            """
        )
        outer.addWidget(self.scroll)
        self.content = QWidget()
        self.content.setStyleSheet("background: transparent;")
        self.scroll.setWidget(self.content)
        content_lay = QVBoxLayout(self.content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(8)
        self.title = QLabel("事件细节")
        self.title.setStyleSheet(f"{FONT_STACK_QSS} font-size: 14px; font-weight: 700; color: #111820;")
        self.title.setVisible(False)
        content_lay.addWidget(self.title)
        self.body = QLabel()
        self.body.setTextFormat(Qt.RichText)
        self.body.setWordWrap(True)
        self.body.setOpenExternalLinks(True)
        self.body.setTextInteractionFlags(Qt.TextBrowserInteraction | Qt.LinksAccessibleByMouse)
        self.body.setStyleSheet(f"{FONT_STACK_QSS} color: #111820; background: transparent;")
        content_lay.addWidget(self.body)
        self.sub_area = QWidget()
        self.sub_area.setStyleSheet("background: transparent;")
        self.sub_col = QVBoxLayout(self.sub_area)
        self.sub_col.setContentsMargins(0, 2, 0, 0)
        self.sub_col.setSpacing(5)
        content_lay.addWidget(self.sub_area)
        ink = getattr(getattr(parent_window, "app", None), "ink", None)
        self.apply_ink_theme(ink.theme if ink is not None and ink.active else None)

    @staticmethod
    def _panel_qss(theme: "InkTheme | None") -> str:
        background = css_rgba(qcolor(theme.popup_bg), 0.96) if theme else "rgba(248,252,255,242)"
        return (
            f"QFrame#detailsPanel {{ {FONT_STACK_QSS} border-radius: 22px;"
            f" border: 1px solid rgba(255,255,255,170); background: {background}; }}"
            f" QCheckBox {{ {FONT_STACK_QSS} color: #111820; font-size: 13px; }}"
            f" QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px;"
            f" border: 1px solid rgba(25,35,45,120); background: rgba(255,255,255,190); }}"
            f" QCheckBox::indicator:checked {{ background: #111820; image: none; }}"
        )

    def apply_ink_theme(self, theme: "InkTheme | None") -> None:
        self.panel.setStyleSheet(self._panel_qss(theme))

    def _render(self, todo: TodoItem) -> None:
        self._todo_id = todo.id
        has_details, has_subs = bool(todo.details.strip()), bool(todo.subtasks)
        self.title.setVisible(has_details and has_subs)
        self.body.setText(details_rich_text(todo.details) if has_details else "")
        self.body.setVisible(has_details)
        while self.sub_col.count():
            item = self.sub_col.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._sub_checks = {}
        for sub in todo.subtasks:
            check = QCheckBox(sub.text)
            check.setChecked(sub.done)
            check.setCursor(Qt.PointingHandCursor)
            check.toggled.connect(
                lambda checked, sub_id=sub.id: self.parent_window.complete_subtask(self._todo_id or "", sub_id, checked)
            )
            self._sub_checks[sub.id] = check
            self.sub_col.addWidget(check)
        self.sub_area.setVisible(bool(todo.subtasks))

    def sync_subtask(self, todo_id: str, subtask_id: str, checked: bool) -> None:
        """Keep the popup's checkbox in sync when a subtask toggles elsewhere (row / editor)."""
        if self._todo_id != todo_id:
            return
        check = self._sub_checks.get(subtask_id)
        if check is not None:
            check.blockSignals(True)
            check.setChecked(checked)
            check.blockSignals(False)

    def show_for(self, todo: TodoItem, anchor: QPoint) -> None:
        if not (todo.details.strip() or todo.subtasks):
            return
        self._cancel_close()
        self._render(todo)
        screen = QApplication.primaryScreen().availableGeometry()
        width = min(560, max(320, screen.width() - 24))
        inner = width - 40  # panel margins (20 left + 20 right)
        # Measure real content height: rich-text QLabels need heightForWidth at the actual
        # width — sizeHint()/adjustSize() report the unwrapped one-line height and clipped
        # the markdown to a sliver.
        # isHidden(), not isVisible(): the dialog isn't shown yet at measure time, so
        # isVisible() (ancestor-dependent) is always False here and everything measured 0.
        content_height = 0
        if not self.title.isHidden():
            content_height += self.title.sizeHint().height() + 8
        if not self.body.isHidden():
            self.body.setFixedWidth(inner)
            content_height += max(
                details_html_height(self.body.text(), inner, self.body.font()),
                self.body.sizeHint().height(),
            ) + 8
        if not self.sub_area.isHidden():
            content_height += self.sub_area.sizeHint().height() + 2
        height = min(max(content_height + 32, 60), int(screen.height() * 0.75))
        self.setFixedSize(width, height)
        self.panel.setGeometry(0, 0, width, height)
        x = min(max(anchor.x() + 14, screen.left() + 8), screen.right() - width - 8)
        y = min(max(anchor.y() + 14, screen.top() + 8), screen.bottom() - height - 8)
        self.move(x, y)
        self.show()
        self.raise_()

    def schedule_close(self, delay_ms: int = 320) -> None:
        """Close after a short grace period instead of instantly: the popup floats a dozen
        pixels away from the row, so moving the mouse over to it (to scroll the details) has
        to survive the row's leave event. If the cursor is over the popup when the timer
        fires, stay open — its own leave re-schedules the close."""
        if not self.isVisible():
            return
        self._cancel_close()
        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.timeout.connect(self._close_if_not_hovered)
        self._close_timer.start(delay_ms)

    def _cancel_close(self) -> None:
        if self._close_timer is not None:
            self._close_timer.stop()
            self._close_timer = None

    def _close_if_not_hovered(self) -> None:
        if self.isVisible() and not self.geometry().contains(QCursor.pos()):
            self.hide()

    def leaveEvent(self, event) -> None:
        self.schedule_close()
        super().leaveEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)


class TodoEditorPopup(QDialog):
    """Add or edit a todo: 内容 / 地点(可选) / DDL(可选). Shared by the "+" add flow and the
    per-row pencil edit. Qt.Tool (not Qt.Popup) so the QLineEdits reliably get keyboard focus
    on Windows; the WindowDeactivate handler gives click-outside-to-dismiss."""

    def __init__(self, parent_window: "MemoWindow") -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.parent_window = parent_window
        self._edit_id: str | None = None  # None = add mode, else the todo being edited
        self.setWindowTitle("添加事项")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.StrongFocus)
        self._width = 460  # clamped base width; height grows with the expanded sections
        self._subtask_rows: list[dict] = []

        self.panel = QFrame(self)
        self.panel.setObjectName("addPanel")
        self.panel.setGeometry(0, 0, self.width(), self.height())
        self.panel.setStyleSheet(
            f"""
            QFrame#addPanel {{
                {FONT_STACK_QSS}
                border-radius: 22px;
                border: 1px solid rgba(255,255,255,170);
                background: rgba(248,252,255,238);
            }}
            QLineEdit {{
                {FONT_STACK_QSS}
                border: 1px solid rgba(255,255,255,145);
                border-radius: 17px;
                background: rgba(255,255,255,150);
                color: #111820;
                font-size: {POPUP_INPUT_FONT_PX}px;
                padding: 9px 14px;
                selection-background-color: rgba(33,150,243,120);
            }}
            """
        )
        # No add_soft_shadow: this panel fills the tool window, so the shadow would be clipped
        # (invisible) while the effect re-renders + re-blurs the subtree on every repaint.

        outer = QVBoxLayout(self.panel)
        outer.setContentsMargins(18, 14, 14, 14)
        outer.setSpacing(10)
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入事项")
        self.input.returnPressed.connect(self.accept)
        outer.addWidget(self.input)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.location_input = QLineEdit()
        self.location_input.setPlaceholderText("地点（可选）")
        self.location_input.returnPressed.connect(self.accept)
        row.addWidget(self.location_input, 1)
        self.ddl_input = QLineEdit()
        self.ddl_input.setPlaceholderText("DDL（可选）")
        self.ddl_input.setFixedWidth(150)
        self.ddl_input.returnPressed.connect(self.accept)
        row.addWidget(self.ddl_input)
        self.ok = RoundButton("✓", 46, tone="confirm")
        self.ok.clicked.connect(self.accept)
        row.addWidget(self.ok)
        outer.addLayout(row)

        # ── 扩展区: 细节 / 子任务 / 重复 ─────────────────────────────────────────────────
        toggles = QHBoxLayout()
        toggles.setContentsMargins(0, 0, 0, 0)
        toggles.setSpacing(8)
        self.details_toggle = self._make_chip_button("添加细节")
        self.details_toggle.clicked.connect(self._toggle_details)
        toggles.addWidget(self.details_toggle)
        self.subtask_toggle = self._make_chip_button("子任务")
        self.subtask_toggle.clicked.connect(self._toggle_subtasks)
        toggles.addWidget(self.subtask_toggle)
        self.recur_toggle = self._make_chip_button("重复")
        self.recur_toggle.clicked.connect(self._toggle_recur)
        toggles.addWidget(self.recur_toggle)
        toggles.addStretch()
        outer.addLayout(toggles)

        self.details_edit = QPlainTextEdit()
        self.details_edit.setPlaceholderText("记录细节，支持 Markdown 语法")
        self.details_edit.setVisible(False)
        self.details_edit.setFixedHeight(190)
        self.details_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.details_edit.setStyleSheet(
            f"""
            QPlainTextEdit {{
                {FONT_STACK_QSS}
                border: 1px solid rgba(255,255,255,145);
                border-radius: 12px;
                background: rgba(255,255,255,150);
                color: #111820;
                font-size: {POPUP_INPUT_FONT_PX}px;
                padding: 8px 12px;
                selection-background-color: rgba(33,150,243,120);
            }}
            """
        )
        outer.addWidget(self.details_edit)

        self.subtask_box = QWidget()
        self.subtask_box.setStyleSheet("background: transparent;")
        self.subtask_list = QVBoxLayout(self.subtask_box)
        self.subtask_list.setContentsMargins(0, 0, 0, 0)
        self.subtask_list.setSpacing(6)
        self.subtask_box.setVisible(False)
        outer.addWidget(self.subtask_box)

        add_row = QHBoxLayout()
        add_row.setContentsMargins(0, 0, 0, 0)
        self.add_subtask_btn = self._make_chip_button("+ 添加子任务")
        self.add_subtask_btn.clicked.connect(lambda: self._add_subtask_row())
        add_row.addWidget(self.add_subtask_btn)
        add_row.addStretch()
        self.subtask_list.addLayout(add_row)

        self.recur_box = QWidget()
        self.recur_box.setStyleSheet("background: transparent;")
        recur_row = QHBoxLayout(self.recur_box)
        recur_row.setContentsMargins(0, 0, 0, 0)
        recur_row.setSpacing(8)
        # Plain Qt combo/spin pick up the SYSTEM palette — on a dark-mode Windows the closed
        # control renders white-on-transparent and the pop-up list renders black, clashing with
        # the light panel. Pin the app's light chips explicitly (incl. the item view, so the
        # drop-down itself is light too).
        recur_light_qss = (
            f"""
            QComboBox, QSpinBox {{
                {FONT_STACK_QSS}
                font-size: 17px; color: #2a3644;
                background: rgba(255,255,255,190);
                border: 1px solid rgba(20,30,40,45); border-radius: 16px;
                padding: 4px 14px;
            }}
            QComboBox:hover, QSpinBox:hover {{ background: rgba(255,255,255,245); }}
            QComboBox::drop-down {{ border: none; width: 30px; }}
            QComboBox::down-arrow {{
                image: none; width: 0; height: 0;
                border-left: 5px solid transparent; border-right: 5px solid transparent;
                border-top: 6px solid rgba(17,24,32,150); margin-right: 10px;
            }}
            QComboBox QAbstractItemView {{
                {FONT_STACK_QSS}
                font-size: 17px; color: #2a3644; background: #ffffff;
                border: 1px solid rgba(20,30,40,60); border-radius: 10px;
                selection-background-color: rgba(232,93,147,45); selection-color: #111820;
                outline: none;
            }}
            QSpinBox::up-button, QSpinBox::down-button {{ width: 0; }}
            """
        )
        self.recur_combo = QComboBox()
        for token, label in RECUR_CHOICES:
            self.recur_combo.addItem(label, token)
        # Custom interval entry: selecting it reveals the count/unit spinners ("every:Nd/Nw").
        self.recur_combo.addItem("自定义…", "every:")
        self.recur_combo.setCurrentIndex(0)
        self.recur_combo.currentIndexChanged.connect(self._recur_changed)
        self.recur_combo.setStyleSheet(recur_light_qss)
        self.recur_combo.setCursor(Qt.PointingHandCursor)
        recur_row.addWidget(self.recur_combo, 1)
        # Custom "every:Nd/Nw" interval: a spin count plus a 天/周 unit picker.
        self.recur_count = QSpinBox()
        self.recur_count.setRange(1, 365)
        self.recur_count.setValue(1)
        self.recur_count.setVisible(False)
        self.recur_count.setStyleSheet(recur_light_qss)
        recur_row.addWidget(self.recur_count)
        self.recur_unit = QComboBox()
        self.recur_unit.addItem("天", "d")
        self.recur_unit.addItem("周", "w")
        self.recur_unit.setVisible(False)
        self.recur_unit.setStyleSheet(recur_light_qss)
        self.recur_unit.setCursor(Qt.PointingHandCursor)
        recur_row.addWidget(self.recur_unit)
        self.recur_box.setVisible(False)
        outer.addWidget(self.recur_box)

        self._detail_open = False
        self._subtasks_open = False
        self._recur_open = False
        self._custom_every = False
        self._relayout()
        ink = getattr(getattr(parent_window, "app", None), "ink", None)
        self.apply_ink_theme(ink.theme if ink is not None and ink.active else None)

    def apply_ink_theme(self, theme: "InkTheme | None") -> None:
        background = css_rgba(qcolor(theme.popup_bg), 0.95) if theme else "rgba(248,252,255,238)"
        field = "rgba(255,255,255,185)" if theme else "rgba(255,255,255,150)"
        color = theme.text if theme else "#111820"
        selection = css_rgba(qcolor(theme.accent), 0.47) if theme else "rgba(232,93,147,120)"
        self.panel.setStyleSheet(
            f"QFrame#addPanel {{ {FONT_STACK_QSS} border-radius: 22px; border: 1px solid rgba(255,255,255,170); background: {background}; }}"
            f" QLineEdit {{ {FONT_STACK_QSS} border: 1px solid rgba(255,255,255,145); border-radius: 17px; background: {field}; color: {color}; font-size: {POPUP_INPUT_FONT_PX}px; padding: 9px 14px; selection-background-color: {selection}; }}"
            f" QPlainTextEdit {{ {FONT_STACK_QSS} border: 1px solid rgba(255,255,255,145); border-radius: 12px; background: {field}; color: {color}; font-size: {POPUP_INPUT_FONT_PX}px; padding: 8px 12px; selection-background-color: {selection}; }}"
        )

    def _make_chip_button(self, text: str) -> QPushButton:
        # Compact neutral chip (17px) instead of the 27px fluent PushButton: the toggle row is
        # a secondary affordance and should read quieter than the primary input fields.
        button = QPushButton(text, self.panel)
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedHeight(34)
        button.setStyleSheet(
            f"""
            QPushButton {{
                {FONT_STACK_QSS}
                font-size: 17px;
                color: #2a3644;
                background: rgba(255,255,255,175);
                border: 1px solid rgba(20,30,40,42);
                border-radius: 16px;
                padding: 4px 18px;
            }}
            QPushButton:hover {{ background: rgba(255,255,255,240); }}
            QPushButton:checked {{
                color: #b8467a;
                background: rgba(232,93,147,32);
                border-color: rgba(232,93,147,150);
                font-weight: 600;
            }}
            """
        )
        return button

    def _position(self, point: QPoint, width: int) -> None:
        self._width = max(420, min(600, width))
        self._relayout()
        self.move(point)

    def _toggle_details(self) -> None:
        self._detail_open = not self._detail_open
        self.details_toggle.setChecked(self._detail_open)
        self.details_edit.setVisible(self._detail_open)
        self._relayout()

    def _toggle_subtasks(self) -> None:
        self._subtasks_open = not self._subtasks_open
        self.subtask_toggle.setChecked(self._subtasks_open)
        self.subtask_box.setVisible(self._subtasks_open)
        if self._subtasks_open and not self._subtask_rows:
            self._add_subtask_row()
        self._relayout()

    def _toggle_recur(self) -> None:
        self._recur_open = not self._recur_open
        self.recur_toggle.setChecked(self._recur_open)
        self.recur_box.setVisible(self._recur_open)
        self._relayout()

    def _recur_changed(self) -> None:
        token = self.recur_combo.currentData()
        self._custom_every = token == "every:"
        self.recur_count.setVisible(self._custom_every)
        self.recur_unit.setVisible(self._custom_every)
        self._relayout()

    def _add_subtask_row(self, text: str = "", sub_id: str | None = None) -> None:
        # Programmatic adds (open_edit prefill, "+ 添加子任务") imply the section is open.
        self._subtasks_open = True
        self.subtask_box.setVisible(True)
        row_widget = QWidget(self.subtask_box)
        h = QHBoxLayout(row_widget)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        edit = QLineEdit()
        edit.setPlaceholderText("子任务内容")
        edit.setFixedHeight(40)
        edit.returnPressed.connect(self.accept)
        h.addWidget(edit, 1)
        entry = {"id": sub_id or str(uuid4()), "widget": row_widget, "edit": edit}
        up = RoundButton("↑", SUBTASK_BTN_SIZE, parent=row_widget)
        up.clicked.connect(lambda: self._move_subtask(entry, -1))
        h.addWidget(up)
        down = RoundButton("↓", SUBTASK_BTN_SIZE, parent=row_widget)
        down.clicked.connect(lambda: self._move_subtask(entry, 1))
        h.addWidget(down)
        delete = RoundButton("✕", SUBTASK_BTN_SIZE, parent=row_widget)
        delete.clicked.connect(lambda: self._remove_subtask(entry))
        h.addWidget(delete)
        edit.setText(text)
        # Row widgets sit after the leading "+ 添加子任务" row inside subtask_list.
        self.subtask_list.insertWidget(len(self._subtask_rows) + 1, row_widget)
        self._subtask_rows.append(entry)
        self._relayout()

    def _remove_subtask(self, entry: dict) -> None:
        if entry in self._subtask_rows:
            self._subtask_rows.remove(entry)
            entry["widget"].deleteLater()
            self._relayout()

    def _move_subtask(self, entry: dict, delta: int) -> None:
        index = self._subtask_rows.index(entry)
        target = index + delta
        if not 0 <= target < len(self._subtask_rows):
            return
        self._subtask_rows[index], self._subtask_rows[target] = self._subtask_rows[target], self._subtask_rows[index]
        self.subtask_list.removeWidget(entry["widget"])
        self.subtask_list.insertWidget(target + 1, entry["widget"])
        self._relayout()

    def _current_subtasks(self) -> list[SubTask]:
        subs: list[SubTask] = []
        for order, entry in enumerate(self._subtask_rows):
            text = entry["edit"].text().strip()
            if text:
                subs.append(SubTask(id=entry["id"], text=text, order=order))
        return subs

    def _current_recur(self) -> str:
        if not self._recur_open:
            return "none"
        token = self.recur_combo.currentData()
        if token == "every:":
            unit = self.recur_unit.currentData() or "d"
            return parse_recur(f"every:{self.recur_count.value()}{unit}") or "none"
        return token or "none"

    def _relayout(self) -> None:
        # Size now, then again on the next event-loop pass: widget sizeHints (QPlainTextEdit,
        # freshly shown sections) only settle after polishing, and a single synchronous pass
        # measures them stale — that's what overlapped the sections.
        self._sync_size()
        QTimer.singleShot(0, self._sync_size)

    def _sync_size(self) -> None:
        layout = self.panel.layout()
        layout.invalidate()
        layout.activate()
        self.panel.adjustSize()
        height = max(132, self.panel.sizeHint().height())
        self.setFixedSize(self._width, height)
        self.panel.setGeometry(0, 0, self._width, height)

    def _reset_fields(self) -> None:
        self._detail_open = False
        self._subtasks_open = False
        self._recur_open = False
        self._custom_every = False
        self.details_toggle.setChecked(False)
        self.subtask_toggle.setChecked(False)
        self.recur_toggle.setChecked(False)
        self.details_edit.setVisible(False)
        self.subtask_box.setVisible(False)
        self.recur_box.setVisible(False)
        self.recur_count.setVisible(False)
        self.recur_unit.setVisible(False)
        self.details_edit.clear()
        while len(self._subtask_rows):
            self._remove_subtask(self._subtask_rows[0])
        self.recur_combo.blockSignals(True)
        self.recur_combo.setCurrentIndex(0)
        self.recur_combo.blockSignals(False)
        self.recur_count.setValue(1)
        self.recur_unit.setCurrentIndex(0)

    def _show_focused(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(0, lambda: (self.input.setFocus(Qt.PopupFocusReason), self.input.selectAll()))

    def open_add(self, point: QPoint, width: int) -> None:
        self._edit_id = None
        self.setWindowTitle("添加事项")
        self.input.clear()
        self.location_input.clear()
        self.ddl_input.clear()
        self._reset_fields()
        self._position(point, width)
        self._show_focused()

    def open_edit(
        self,
        todo: "TodoItem",
        point: QPoint,
        width: int,
    ) -> None:
        """Prefill the editor from a full TodoItem (text/location/ddl + details/subtasks/recur)."""
        self._edit_id = todo.id
        self.setWindowTitle("编辑事项")
        self.input.setText(todo.text)
        self.location_input.setText(todo.location)
        self.ddl_input.setText(todo.ddl)
        self._reset_fields()
        if todo.details:
            self.details_edit.setPlainText(todo.details)
            self._detail_open = True
            self.details_edit.setVisible(True)
        if todo.subtasks:
            self._subtasks_open = True
            self.subtask_box.setVisible(True)
            for sub in todo.subtasks:
                self._add_subtask_row(sub.text, sub.id)
        if parse_recur(todo.recur) not in (None, "none"):
            self._recur_open = True
            self.recur_box.setVisible(True)
            token = todo.recur
            if token.startswith("every:"):
                self.recur_combo.setCurrentIndex(max(0, self.recur_combo.findData("every:")))
                spec = token[len("every:"):]
                self.recur_count.setValue(int(spec[:-1]))
                self.recur_unit.setCurrentIndex(max(0, self.recur_unit.findData(spec[-1])))
            else:
                self.recur_combo.setCurrentIndex(max(0, self.recur_combo.findData(token)))
        self._position(point, width)
        self._show_focused()

    def event(self, event) -> bool:
        if event.type() == QEvent.WindowDeactivate:
            self.hide()
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def accept(self) -> None:
        text = self.input.text().strip()
        location = self.location_input.text().strip()
        ddl = self.ddl_input.text().strip()
        details = self.details_edit.toPlainText().strip() if self._detail_open else ""
        subtasks = self._current_subtasks() if self._subtasks_open else []
        recur = self._current_recur()
        if text:
            if self._edit_id is not None:
                self.parent_window.update_todo(
                    self._edit_id, text, location, ddl,
                    details=details, subtasks=subtasks, recur=recur,
                )
            else:
                self.parent_window.add_todo(text, location, ddl, details=details, subtasks=subtasks, recur=recur)
        self._edit_id = None
        self.hide()


class HistoryWindow(QDialog):
    def __init__(self, app: "LiquidMemoApp") -> None:
        super().__init__(None, Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.app = app
        self.setWindowTitle("历史记录")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(scaled_dialog_size(620, 620))
        self._build()

    def _build(self) -> None:
        self.frame = QFrame(self)
        self.frame.setObjectName("fluentPanel")
        self.frame.setGeometry(0, 0, self.width(), self.height())
        self.frame.setStyleSheet(
            f"""
            QFrame#fluentPanel {{
                {FONT_STACK_QSS}
                background: rgb(246, 248, 252);
                border: 1px solid rgba(255,255,255,185);
                border-radius: 22px;
            }}
            """
        )
        # No add_soft_shadow: full-bleed frame → shadow clipped (invisible) but still taxes
        # every child repaint with a full re-render + blur (same jank as the settings window).
        layout = QVBoxLayout(self.frame)
        layout.setContentsMargins(38, 34, 38, 38)
        layout.setSpacing(24)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        title = TitleLabel("历史记录")
        set_label_font(title, SETTING_TITLE_FONT_PX)
        subtitle = BodyLabel("已归档的待办事项可以随时恢复。")
        set_label_font(subtitle, SETTING_STATUS_FONT_PX, color="rgba(17,24,32,150)")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)

        clear = PushButton("清空", self.frame, FluentIcon.DELETE)
        clear.clicked.connect(self._clear)
        enlarge_control_font(clear)
        header.addWidget(clear)
        close = PrimaryPushButton("完成", self.frame, FluentIcon.ACCEPT)
        close.clicked.connect(self.hide)
        enlarge_control_font(close)
        header.addWidget(close)
        layout.addLayout(header)

        self.scroll = SmoothScrollArea(self.frame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.content = QWidget()
        self.content.setStyleSheet("background: transparent;")
        self.list = QVBoxLayout(self.content)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(10)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)
        self.refresh()
        ink = getattr(self.app, "ink", None)
        self.apply_ink_theme(ink.theme if ink is not None and ink.active else None)

    def apply_ink_theme(self, theme: "InkTheme | None") -> None:
        background = (
            f"qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {theme.panel_top},stop:1 {theme.panel_bottom})"
            if theme else "rgb(246,248,252)"
        )
        self.frame.setStyleSheet(
            f"QFrame#fluentPanel {{ {FONT_STACK_QSS} background: {background}; border: 1px solid rgba(255,255,255,185); border-radius: 22px; }}"
        )

    def refresh(self) -> None:
        while self.list.count():
            item = self.list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self.app.state.history:
            empty = CardWidget()
            empty_layout = QVBoxLayout(empty)
            empty_layout.setContentsMargins(22, 22, 22, 22)
            title = BodyLabel("暂无历史事项")
            title.setAlignment(Qt.AlignCenter)
            set_label_font(title, SETTING_ROW_TITLE_FONT_PX)
            detail = QLabel("勾选完成并归档后的待办会显示在这里。")
            detail.setAlignment(Qt.AlignCenter)
            detail.setStyleSheet(
                f"{FONT_STACK_QSS} color: rgba(17,24,32,135); font-size: {SETTING_STATUS_FONT_PX}px;"
            )
            empty_layout.addWidget(title)
            empty_layout.addWidget(detail)
            self.list.addWidget(empty)
            self.list.addStretch()
            return

        for todo in reversed(self.app.state.history[-30:]):
            card = CardWidget()
            row_layout = QHBoxLayout(card)
            row_layout.setContentsMargins(24, 18, 20, 18)
            row_layout.setSpacing(18)

            text_layout = QVBoxLayout()
            text_layout.setSpacing(4)
            label = BodyLabel(todo.text)
            label.setWordWrap(True)
            set_label_font(label, SETTING_CONTROL_FONT_PX)
            meta = QLabel("已完成" if not todo.completedAt else f"完成于 {todo.completedAt[:10]}")
            meta.setStyleSheet(
                f"{FONT_STACK_QSS} color: rgba(17,24,32,130); font-size: {SETTING_STATUS_FONT_PX}px;"
            )
            text_layout.addWidget(label)
            text_layout.addWidget(meta)
            row_layout.addLayout(text_layout, 1)

            restore = PushButton("恢复", card, FluentIcon.RETURN)
            restore.clicked.connect(lambda _=False, todo_id=todo.id: self._restore(todo_id))
            enlarge_control_font(restore)
            row_layout.addWidget(restore)
            self.list.addWidget(card)
        self.list.addStretch()

    def _restore(self, todo_id: str) -> None:
        self.app.restore_from_history(todo_id)
        self.refresh()

    def _clear(self) -> None:
        self.app.state.history.clear()
        self.app.save()
        self.refresh()


# Fixed vertical chrome inside the window that is NOT glass padding: the top bar (drag handle +
# buttons), its spacing to the list, and the scroll's inner margins. The window height is solved
# so that, after the proportional glass padding, this block + the rows + corner margin all fit
# inside the glass. (Originally folded into a 104px magic constant alongside the static margins.)
MEMO_TOP_BLOCK = 68


class AcrylicSkin:
    """Frosted-glass skin: the window is a translucent DWM acrylic surface (rounded by DWM) with
    no GPU screen capture and no effect chain — so the whole window IS the surface
    (geometry_scale = 1.0) and content fills it with only a small corner margin."""

    kind = "acrylic"
    geometry_scale = 1.0
    corner_margin = 14
    # None -> use settings.windowTint for the frost and pick text deterministically by luminance.
    # InkSkin overrides these so the themed look isn't special-cased across the dispatch.
    acrylic_tint: "str | None" = None
    text_override: "str | None" = None

    def vertical_padding(self, height: int) -> int:
        return 0

    def horizontal_padding(self, width: int) -> int:
        return 0


# Acrylic frost tint opacity (alpha over the blurred desktop). Kept at a readability floor so
# even a busy/terminal desktop behind the window is pressed into a near-uniform surface.
ACRYLIC_TINT_ALPHA = 0xB3  # ~0.70
# Deterministic text colors for the acrylic skin, chosen by the frost tint's luminance: a soft
# near-black on light frost, a soft near-white on dark frost (not pure #000/#FFF — calmer).
ACRYLIC_TEXT_DARK = "#1B2127"
ACRYLIC_TEXT_LIGHT = "#E8ECEF"


class InkSkin:
    """Animated ink-wash surface (灵动水墨). Its colour (and the overlay-safe text colour)
    follow the chosen 水墨主题."""

    kind = "ink"
    geometry_scale = 1.0
    corner_margin = 14

    def __init__(self, text_override: "str | None" = None) -> None:
        self.text_override = text_override or swirl_tokens("qinghua").text_overlay_safe

    def vertical_padding(self, height: int) -> int:
        return 0

    def horizontal_padding(self, width: int) -> int:
        return 0


class ImageSkin:
    """User image-background skin: a static, cover-scaled image painted below the content layer.
    Like AcrylicSkin there is no GPU screen capture and no effect chain — the window IS the
    surface (geometry_scale = 1.0, content fills it), rounded by DWM. `image_path` is the
    resolved PNG under state_store.skins_dir(); a missing image makes _make_skin fall back to
    AcrylicSkin so this always points at a readable file when live."""

    kind = "image"
    geometry_scale = 1.0
    corner_margin = 14

    def __init__(self, image_path: "Path | None" = None) -> None:
        self.image_path = image_path

    def vertical_padding(self, height: int) -> int:
        return 0

    def horizontal_padding(self, width: int) -> int:
        return 0


class _ImageBackground(QWidget):
    """Cover-scaled static image painted below the (capture-excluded) content layer for the image
    skin. Transparent to mouse so the window's native hit-testing still governs clicks; the DWM
    rounded corners clip it to shape, exactly as the acrylic frost is clipped."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._pixmap = QPixmap()

    def set_image(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap if (pixmap is not None and not pixmap.isNull()) else QPixmap()
        self.update()

    def paintEvent(self, event) -> None:
        if self._pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = self.rect()
        # Cover: scale to fill, center-crop the overflow (no distortion, no blank bars).
        scaled = self._pixmap.scaled(rect.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = (scaled.width() - rect.width()) // 2
        y = (scaled.height() - rect.height()) // 2
        painter.drawPixmap(rect, scaled, QRect(x, y, rect.width(), rect.height()))


class MemoWindow(QWidget):
    def __init__(self, app: "LiquidMemoApp") -> None:
        super().__init__()
        self.app = app
        self.skin = self._make_skin(app.state.settings.skin)
        # Tracks which rendering mode is currently live so apply_settings only performs the
        # acrylic<->image transition when the skin actually changes.
        self._active_skin_kind: str | None = None
        self._window_effect = WindowsWindowEffect(self)
        self._acrylic_applied = False
        self._acrylic_signature: str | None = None
        # Image-skin (static background) state. _image_bg paints the cover-scaled image below the
        # content layer; _image_luminance drives the deterministic dark/light text choice.
        self._image_bg: _ImageBackground | None = None
        self._image_pixmap: QPixmap | None = None
        self._image_luminance: float = 1.0
        # Ink-skin background: GPU ink-wash when available, else the QPainter swirl
        # (see ink_background.make_ink_background). Either exposes the same lifecycle.
        # `_ink_theme_key` tracks which 水墨主题 palette the live widget was built for, so a
        # theme switch recolours it in place instead of keeping the stale colours.
        self._ink_bg: QWidget | None = None
        self._ink_theme_key: str | None = None
        self.setWindowTitle("桌面备忘")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")
        self.setMouseTracking(True)
        # All interactive content lives in this transparent child layer, kept out of any screen
        # capture by protect_content_layer(); the window itself only carries the frost / image
        # surface. (Previously provided by the D3D base class; recreated here directly.)
        self.container = QWidget(self)
        self.container.setStyleSheet("background: transparent;")
        self.container.setAttribute(Qt.WA_TranslucentBackground)
        self.container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._rows: dict[str, TodoRow] = {}
        self._event_rows: dict[str, CalendarRow] = {}
        self._calendar_header: QLabel | None = None
        # Expanded mode grows the window to fit all content (no height clamp, no scrollbar, no
        # elided text); collapsed mode keeps the default clamp + scroll behavior.
        self._expanded = False
        self._shown_once = False
        self._window_layer_applied = False
        self._is_window_moving = False
        self._size_at_drag_start = (0, 0)
        self._reorder_row: TodoRow | None = None
        self._build_content()
        # Global wheel hook: scroll the list whenever the cursor is over it, bypassing the
        # click-through hit-testing that otherwise sends wheel events to the desktop below.
        self._wheel_hook = GlobalWheelHook(self._on_global_wheel)
        self._wheel_hook.install()

        # ── Edge auto-hide (dock) state ──────────────────────────────────────────────────
        self._dock_edge: str | None = None      # "left"/"right"/"top" while docked, else None
        self._dock_hidden = False               # True when slid off-screen (only peek showing)
        self._dock_animating = False            # suppresses moveEvent side effects during slide
        self._dock_shown_pos: QPoint | None = None  # flush-against-edge position (fully visible)
        self._slide_anim: QPropertyAnimation | None = None
        # Cursor poll runs only while docked-and-shown: detects the cursor leaving the window so
        # the hide countdown can start (the click-through body never delivers leave events).
        self._dock_poll = QTimer(self)
        self._dock_poll.setInterval(DOCK_POLL_MS)
        self._dock_poll.timeout.connect(self._dock_tick)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._hide_docked)

    @property
    def content(self) -> QWidget:
        return self.container or self

    def _build_content(self) -> None:
        root = self.content
        root.setStyleSheet("background: transparent;")
        self.layout = QVBoxLayout(root)
        self.layout.setContentsMargins(26, 18, 26, 18)
        self.layout.setSpacing(10)

        top = QHBoxLayout()
        self.drag_handle = DragHandle(self)
        top.addWidget(self.drag_handle)
        top.addStretch()
        self.expand_button = RoundButton("▾", tone="neutral")
        self.expand_button.setToolTip("展开全部")
        self.expand_button.clicked.connect(self.toggle_expanded)
        top.addWidget(self.expand_button)
        self.add_button = RoundButton("+", tone="add")
        self.add_button.setToolTip("添加注意事项")
        self.add_button.clicked.connect(self.show_add_popup)
        top.addWidget(self.add_button)
        self.hide_button = RoundButton("–", tone="hide")
        self.hide_button.setToolTip("最小化")
        self.hide_button.clicked.connect(self.app.hide_memo_window)
        top.addWidget(self.hide_button)
        self.layout.addLayout(top)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            " QScrollBar:vertical { width: 9px; background: transparent; margin: 2px 1px; }"
            " QScrollBar::handle:vertical { background: rgba(17,24,32,80); border-radius: 4px; min-height: 36px; }"
            " QScrollBar::handle:vertical:hover { background: rgba(17,24,32,130); }"
            " QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )
        self.list_widget = QWidget()
        self.list_widget.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, LIST_EDGE_PAD, 0, LIST_EDGE_PAD)
        self.list_layout.setSpacing(0)
        self.scroll.setWidget(self.list_widget)
        self.layout.addWidget(self.scroll, 1)

        self.empty = QLabel("暂无待办")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet(f"{FONT_STACK_QSS} color: rgba(17,24,32,120); font-size: {EMPTY_HINT_FONT_PX}px;")
        self.layout.addWidget(self.empty, 1)

        self.add_popup = TodoEditorPopup(self)
        self.details_popup = DetailsPopup(self)

    def show_details_preview(self, row: "TodoRow") -> None:
        """1.0s-hover anchor for TodoRow: show the details/subtask preview near the cursor."""
        if self.add_popup.isVisible():
            return
        self.details_popup.show_for(row.todo, QCursor.pos())

    def hide_details_preview(self) -> None:
        # Grace-period close: the popup floats next to the row, so this fires while the user
        # is travelling toward it — the popup aborts the close when the cursor arrives.
        self.details_popup.schedule_close()

    def close_details_preview(self) -> None:
        self.details_popup.hide()

    def complete_subtask(self, todo_id: str, subtask_id: str, checked: bool) -> None:
        """Toggle one subtask; persists and refreshes ONLY the owning row (no column rebuild)."""
        todo = next((item for item in self.app.state.todos if item.id == todo_id), None)
        if todo is None:
            return
        sub = next((s for s in todo.subtasks if s.id == subtask_id), None)
        if sub is None:
            return
        if sub.done == bool(checked):
            return
        sub.done = bool(checked)
        self.app.save_later()
        row = self._rows.get(todo_id)
        if row is not None:
            row.refresh_subtask_state()
        self.details_popup.sync_subtask(todo_id, subtask_id, checked)
        # 双向半自动: checking the LAST open subtask flashes the parent row but never checks it.
        if checked and todo.subtasks and all(s.done for s in todo.subtasks) and row is not None:
            row.flash_completion()

    def toggle_subtasks_collapsed(self, todo_id: str) -> None:
        todo = next((item for item in self.app.state.todos if item.id == todo_id), None)
        if todo is None or not todo.subtasks:
            return
        todo.subtasksCollapsed = not todo.subtasksCollapsed
        self.app.save_later()
        self.refresh()

    def protect_content_layer(self) -> None:
        # Keep the content layer above the surface and apply the current screen-capture policy (by
        # default the widget is excluded from screenshots / recordings; the 允许被截屏 setting can
        # opt back in — see window_layer.set_capture_exclusion).
        if self.container:
            self.container.raise_()
        protect_window_from_capture(int(self.winId()))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Only spin the swirl when it is the active surface — with another skin selected the
        # ink widget stays allocated but hidden.
        if self._ink_bg is not None and self._active_skin_kind == "ink":
            self._ink_bg.start()
        self.protect_content_layer()
        for delay in (0, 80, 180, 420):
            QTimer.singleShot(delay, self.protect_content_layer)
        # Re-showing (e.g. from the tray) while docked-hidden would otherwise reveal the window
        # at its off-screen position — snap it back out so it is actually visible.
        if self._dock_edge is not None and self._dock_hidden:
            self._reveal_docked()
        if not self._shown_once:
            self._shown_once = True
            self.apply_initial_geometry()
            QTimer.singleShot(80, self.refresh)
            QTimer.singleShot(180, self.apply_text_colors)
            # Restore a dock if the saved position sits against an edge.
            QTimer.singleShot(280, self._maybe_dock)
        QTimer.singleShot(0, self.apply_settings)

    def hideEvent(self, event) -> None:
        # Suspend dock timers/animation while the window is hidden (e.g. from the tray); showEvent
        # restores the dock. _dock_edge/_dock_hidden are kept so the state survives a hide/show.
        self.close_details_preview()
        self._dock_poll.stop()
        self._hide_timer.stop()
        self._cancel_slide()
        self._dock_animating = False
        if self._ink_bg is not None:
            self._ink_bg.stop()
        super().hideEvent(event)

    def cleanup(self) -> None:
        # Called on app shutdown. No GPU/capture resources to release anymore (the D3D engine is
        # gone); just stop the dock timers and any in-flight slide.
        self._dock_poll.stop()
        self._hide_timer.stop()
        self._cancel_slide()
        if self._ink_bg is not None:
            self._ink_bg.cleanup()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if self._shown_once:
            # During a dock slide the window is mid-animation toward an off-screen position; skip
            # the per-move save work (and don't persist off-screen coordinates).
            if self._dock_animating:
                return
            # In floating-launcher mode the panel is an ephemeral popover anchored to the
            # launcher. Do not overwrite the user's normal/edge-mode window position.
            if self.app.state.settings.windowMode == "floatingLauncher":
                return
            self.app.state.window.x = self.x()
            self.app.state.window.y = self.y()
            if self._is_window_moving:
                return
            QTimer.singleShot(0, self.protect_content_layer)
            self.app.save_later()

    def paintEvent(self, event) -> None:
        # Layered translucent windows hit-test per-pixel (UpdateLayeredWindow): fully transparent
        # pixels pass the mouse to the desktop before WM_NCHITTEST runs, which killed the resize
        # hot zones on every skin except ink (its GL surface paints every pixel opaque). Fill the
        # window with 1/255 alpha so the whole rect is hit-testable — visually imperceptible, and
        # the DWM frost still shows through beneath.
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
        super().paintEvent(event)

    def resizeEvent(self, event) -> None:
        # The container fills the window; the image layer (a sibling child) tracks it too so the
        # static background always covers the window.
        super().resizeEvent(event)
        if self.container is not None:
            self.container.setGeometry(0, 0, self.width(), self.height())
        if self._image_bg is not None:
            self._image_bg.setGeometry(0, 0, self.width(), self.height())
        if self._ink_bg is not None:
            self._ink_bg.setGeometry(0, 0, self.width(), self.height())

    def nativeEvent(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            import ctypes
            from ctypes import wintypes

            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
            if msg.message == WM_NCHITTEST:
                x = ctypes.c_short(msg.lParam & 0xFFFF).value
                y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                local = self.mapFromGlobal(QPoint(x, y))
                # While hidden, the on-screen peek strip must be HTCLIENT (not click-through) so
                # the window receives WM_MOUSEMOVE over it and can slide back out.
                if self._dock_hidden and not self._dock_animating and self._peek_rect_local().contains(local):
                    return True, HTCLIENT
                if self.drag_handle.isVisible() and self._rect_for(self.drag_handle).contains(local):
                    return True, HTCAPTION
                if self._point_over_todo_row(local):
                    return True, HTCLIENT
                if self._is_interactive_point(local):
                    return True, HTCLIENT
                zone = self._resize_zone(local)
                if zone is not None:
                    return True, zone
                if self.app.state.settings.layerMode == "alwaysVisibleClickThrough":
                    return True, HTTRANSPARENT
            if msg.message == WM_MOUSEMOVE:
                # Only the peek strip is HTCLIENT while hidden, so any mouse-move here means the
                # cursor reached the strip — slide the window back out.
                if self._dock_hidden and not self._dock_animating:
                    self._show_docked()
            if msg.message == WM_ENTERSIZEMOVE:
                self._begin_window_move()
            elif msg.message == WM_EXITSIZEMOVE:
                QTimer.singleShot(0, self._end_window_move)
        return super().nativeEvent(event_type, message)

    def _resize_zone(self, local: QPoint) -> "int | None":
        """Hit-test the drag-resize hot zones along the bottom corners, returning the WM_NCHITTEST
        code (HTBOTTOMLEFT/HTBOTTOMRIGHT/HTBOTTOM/HTLEFT/HTRIGHT) or None. The two bottom corners
        are RESIZE_CORNER squares for a comfortable diagonal grab, widening out to an
        RESIZE_EDGE strip along the bottom and short RESIZE_SIDE strips up the left/right edges.
        The zones live in the otherwise click-through frame; rows/controls are checked before
        this, and the content inset (OUTER_X / corner margin) keeps them from overlapping.
        Disabled while dock-hidden and in expanded mode (expanded auto-fits its size)."""
        if self._dock_hidden or self._dock_animating or self._expanded:
            return None
        w, h = self.width(), self.height()
        x, y = local.x(), local.y()
        if x <= RESIZE_CORNER and y >= h - RESIZE_CORNER:
            return HTBOTTOMLEFT
        if x >= w - RESIZE_CORNER and y >= h - RESIZE_CORNER:
            return HTBOTTOMRIGHT
        if y >= h - RESIZE_EDGE:
            return HTBOTTOM
        if x <= RESIZE_EDGE and y >= h - RESIZE_SIDE:
            return HTLEFT
        if x >= w - RESIZE_EDGE and y >= h - RESIZE_SIDE:
            return HTRIGHT
        return None

    def _rect_for(self, widget: QWidget) -> QRect:
        top_left = widget.mapTo(self, QPoint(0, 0))
        return QRect(top_left, widget.size())

    def _is_interactive_point(self, point: QPoint) -> bool:
        widgets: list[QWidget] = [self.add_button, self.hide_button, self.expand_button]
        for row in self._rows.values():
            # edit_btn (✎) opens the editor and ddl_label (the DDLCell) also opens it on click;
            # the time cell on calendar rows is display-only, so only their checkbox is interactive.
            widgets.extend([row.checkbox, row.urgent, row.ddl_label, row.edit_btn, row.sub_toggle])
            if row.subtask_area.isVisible():
                widgets.append(row.subtask_area)
                widgets.extend(row.subtask_area.findChildren(QCheckBox))
        for row in self._event_rows.values():
            widgets.append(row.checkbox)
        # Wheel scrolling over the list is handled by the global wheel hook (see
        # _on_global_wheel). Todo rows are handled separately by _point_over_todo_row; outside
        # those rows, only these discrete controls receive clicks.
        return any(widget.isVisible() and self._rect_for(widget).adjusted(-4, -4, 4, 4).contains(point) for widget in widgets)

    def _point_over_todo_row(self, point: QPoint) -> bool:
        """Todo rows receive Qt mouse events for reordering; calendar rows stay click-through."""
        return any(row.isVisible() and self._rect_for(row).contains(point) for row in self._rows.values())

    def begin_system_move(self) -> None:
        self._begin_window_move()
        begin_system_move(int(self.winId()))
        QTimer.singleShot(0, self._end_window_move)

    def _begin_window_move(self) -> None:
        if self._is_window_moving:
            return
        self._is_window_moving = True
        # Remember the size at drag start so _end_window_move can tell a resize loop (native
        # corner/edge drag) apart from a plain move.
        self._size_at_drag_start = (self.width(), self.height())
        # The frost / image surface follows the window natively — nothing to spin up on move.

    def _end_window_move(self) -> None:
        if not self._is_window_moving:
            return
        self._is_window_moving = False
        self.app.state.window.x = self.x()
        self.app.state.window.y = self.y()
        self.app.save_later()
        self.protect_content_layer()
        self._maybe_dock()
        if (self.width(), self.height()) != self._size_at_drag_start:
            self._finish_user_resize()

    def _finish_user_resize(self) -> None:
        """A native corner/edge drag changed the window size: pin it as a manual size so the
        auto content-fit sizing stops overriding it (until 展开全部/收起 resets the choice)."""
        self.app.state.window.userSized = True
        self.app.state.window.width = self.width()
        self.app.state.window.height = self.height()
        self.refresh()

    # ── Edge auto-hide (dock) ────────────────────────────────────────────────────────────
    def _dock_geometry(self) -> QRect:
        screen = self.screen() or QApplication.primaryScreen()
        return screen.availableGeometry()

    def _dock_pos(self, hidden: bool) -> QPoint:
        """The window position for the current dock edge, either flush-visible or slid out to a
        DOCK_PEEK strip. The cross-axis (position along the edge) comes from the snapped shown
        position; the perpendicular axis is recomputed from the live window size."""
        g = self._dock_geometry()
        w, h = self.width(), self.height()
        shown = self._dock_shown_pos or self.pos()
        if self._dock_edge == "left":
            x = (g.left() - w + DOCK_PEEK) if hidden else g.left()
            return QPoint(x, shown.y())
        if self._dock_edge == "right":
            x = (g.left() + g.width() - DOCK_PEEK) if hidden else (g.left() + g.width() - w)
            return QPoint(x, shown.y())
        y = (g.top() - h + DOCK_PEEK) if hidden else g.top()  # "top"
        return QPoint(shown.x(), y)

    def _peek_rect_local(self) -> QRect:
        w, h = self.width(), self.height()
        if self._dock_edge == "left":
            return QRect(w - DOCK_PEEK, 0, DOCK_PEEK, h)
        if self._dock_edge == "right":
            return QRect(0, 0, DOCK_PEEK, h)
        if self._dock_edge == "top":
            return QRect(0, h - DOCK_PEEK, w, DOCK_PEEK)
        return QRect()

    def _maybe_dock(self) -> None:
        # Called after a move ends: dock to the nearest edge within threshold, else undock.
        if self.app.state.settings.windowMode != "edgeHide" or not self.isVisible():
            self._undock()
            return
        g = self._dock_geometry()
        x, y, w, h = self.x(), self.y(), self.width(), self.height()
        gaps = {
            "left": x - g.left(),
            "right": (g.left() + g.width()) - (x + w),
            "top": y - g.top(),
        }
        edge = min(gaps, key=gaps.get)
        if gaps[edge] > DOCK_THRESHOLD:
            self._undock()
            return
        self._dock_edge = edge
        self._dock_hidden = False
        if edge == "left":
            shown = QPoint(g.left(), y)
        elif edge == "right":
            shown = QPoint(g.left() + g.width() - w, y)
        else:
            shown = QPoint(x, g.top())
        self._dock_shown_pos = shown
        if self.pos() != shown:
            self.move(shown)
        self._dock_shown_pos = self.pos()
        self._hide_timer.stop()
        self._dock_poll.start(DOCK_POLL_MS)

    def _undock(self) -> None:
        if self._dock_edge is None:
            self._dock_hidden = False
            return
        if self._dock_hidden:
            self._reveal_docked()  # never leave the window stranded off-screen
        self._dock_edge = None
        self._dock_hidden = False
        self._dock_animating = False
        self._dock_shown_pos = None
        self._dock_poll.stop()
        self._hide_timer.stop()
        self._cancel_slide()

    def _reposition_dock(self) -> None:
        target = self._dock_pos(self._dock_hidden)
        if self.pos() == target:
            return
        if self._dock_hidden:
            self._dock_animating = True
            self.move(target)
            self._dock_animating = False
        else:
            self.move(target)
            self._dock_shown_pos = self.pos()

    def _reveal_docked(self) -> None:
        # Instant (no slide) reveal to the flush position — used on tray re-show and undock.
        self._cancel_slide()
        self._dock_animating = True
        self.move(self._dock_pos(hidden=False))
        self._dock_animating = False
        self._dock_hidden = False
        self.protect_content_layer()
        if self._dock_edge is not None:
            self._dock_poll.start(DOCK_POLL_MS)

    def _hide_docked(self) -> None:
        if self._dock_edge is None or self._dock_hidden or self._dock_animating:
            return
        if self._suppress_hide():
            return
        self._dock_hidden = True
        self._hide_timer.stop()
        # The poll keeps running while hidden: it is what detects the cursor reaching the peek
        # strip. (WM_MOUSEMOVE on the strip proved unreliable in practice — the mostly off-screen
        # HTCLIENT sliver does not dependably receive mouse messages.)
        self._animate_to(self._dock_pos(hidden=True))

    def _show_docked(self) -> None:
        if self._dock_edge is None or not self._dock_hidden or self._dock_animating:
            return
        self._dock_hidden = False
        self._animate_to(self._dock_pos(hidden=False))
        self._dock_poll.start(DOCK_POLL_MS)

    def _animate_to(self, target: QPoint) -> None:
        self._cancel_slide()
        if self.pos() == target:
            self._on_slide_finished()
            return
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(DOCK_SLIDE_MS)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(self.pos())
        anim.setEndValue(target)
        anim.finished.connect(self._on_slide_finished)
        self._dock_animating = True
        self._slide_anim = anim
        anim.start(QPropertyAnimation.DeleteWhenStopped)

    def _cancel_slide(self) -> None:
        if self._slide_anim is not None:
            try:
                self._slide_anim.stop()
            except RuntimeError:
                pass
            self._slide_anim = None

    def _on_slide_finished(self) -> None:
        self._dock_animating = False
        self._slide_anim = None
        self.protect_content_layer()

    def _peek_rect_global(self) -> QRect:
        # The on-screen strip of the hidden window, in global coordinates, with a small inward
        # tolerance so the cursor doesn't have to land on the exact 5px sliver.
        g = self._dock_geometry()
        if self._dock_edge == "left":
            return QRect(g.left(), self.y(), DOCK_PEEK + 2, self.height())
        if self._dock_edge == "right":
            return QRect(g.left() + g.width() - DOCK_PEEK - 2, self.y(), DOCK_PEEK + 2, self.height())
        if self._dock_edge == "top":
            return QRect(self.x(), g.top(), self.width(), DOCK_PEEK + 2)
        return QRect()

    def _dock_tick(self) -> None:
        # Poll while docked. Shown: start the hide countdown when the cursor leaves the window.
        # Hidden: watch for the cursor reaching the peek strip and slide back out.
        if self._dock_edge is None or self._dock_animating:
            return
        cursor = QCursor.pos()
        if self._dock_hidden:
            if self._peek_rect_global().contains(cursor):
                self._show_docked()
            return
        if self._suppress_hide():
            self._hide_timer.stop()
            return
        inside = self.frameGeometry().adjusted(-3, -3, 3, 3).contains(cursor)
        if inside:
            self._hide_timer.stop()
        elif not self._hide_timer.isActive():
            self._hide_timer.start(DOCK_HIDE_DELAY_MS)

    def _suppress_hide(self) -> bool:
        return (
            self._is_window_moving
            or self._reorder_row is not None
            or self.add_popup.isVisible()
            or self.app.settings_window.isVisible()
            or self.app.history_window.isVisible()
        )

    def apply_initial_geometry(self) -> None:
        self.refresh()
        screen = QApplication.primaryScreen().availableGeometry()
        state = self.app.state.window
        if state.startPosition == "last" and state.x is not None and state.y is not None:
            self.move(state.x, state.y)
            return
        if state.startPosition == "current" and state.x is not None and state.y is not None:
            self.move(state.x, state.y)
            return
        x = screen.right() - self.width() - 32 if "Right" in state.startPosition else screen.left() + 32
        y = screen.bottom() - self.height() - 32 if "bottom" in state.startPosition else screen.top() + 32
        self.move(x, y)

    def _make_skin(self, skin_name: str):
        if skin_name == "ink":
            # 灵动水墨 is a normal, always-available skin; the 水墨主题 setting picks its palette.
            key = self.app.state.settings.inkTheme
            return InkSkin(text_override=swirl_tokens(key).text_overlay_safe)
        if skin_name.startswith("image:"):
            skin_id = skin_name[len("image:"):]
            custom = next((s for s in self.app.state.settings.customSkins if s.id == skin_id), None)
            if custom is not None and custom.image_path().exists():
                return ImageSkin(custom.image_path())
            return AcrylicSkin()  # missing/deleted image -> safe fallback to the frost skin
        return AcrylicSkin()

    def apply_settings(self, refresh_rows: bool = False) -> None:
        settings = self.app.state.settings
        # _make_skin resolves a missing image skin down to AcrylicSkin, so branch on the resolved
        # skin's kind (not the raw "image:<id>" string) for both dispatch and change detection —
        # otherwise "image" never equals "image:<id>" and skin_changed misfires.
        self.skin = self._make_skin(settings.skin)
        new_kind = self.skin.kind
        skin_changed = self._active_skin_kind not in (None, new_kind)
        if new_kind == "image":
            self._apply_image_mode()
        elif new_kind == "ink":
            self._apply_ink_mode()
        else:
            self._apply_acrylic_mode()
        # Frost and image use the same full-fill geometry, but switching between them swaps the
        # background layer, so a skin change still needs a relayout even if not explicitly asked.
        if refresh_rows or skin_changed:
            self.refresh()
        self.protect_content_layer()
        self.apply_window_layer()
        if settings.windowMode != "edgeHide":
            self._undock()

    def _apply_acrylic_mode(self) -> None:
        # Frosted mode: the window is a translucent DWM acrylic surface; the image layer (if any)
        # is hidden so the frost shows through.
        self._active_skin_kind = "acrylic"
        self._hide_image_bg()
        self._hide_ink_bg()
        self._apply_acrylic_effect()
        self.apply_text_colors()

    def _ensure_image_bg(self) -> "_ImageBackground":
        if self._image_bg is None:
            self._image_bg = _ImageBackground(self)
            self._image_bg.setGeometry(0, 0, self.width(), self.height())
        return self._image_bg

    def _hide_image_bg(self) -> None:
        if self._image_bg is not None:
            self._image_bg.hide()

    def _ensure_ink_bg(self, theme_key: str) -> QWidget:
        if self._ink_bg is None:
            self._ink_bg = make_ink_background(self, theme_key)
            self._ink_theme_key = theme_key
            self._ink_bg.setGeometry(0, 0, self.width(), self.height())
        elif self._ink_theme_key != theme_key:
            # Recolour in place so the ink-theme switch doesn't tear down / rebuild the GL context.
            self._ink_bg.set_theme(theme_key)
            self._ink_theme_key = theme_key
        return self._ink_bg

    def _hide_ink_bg(self) -> None:
        if self._ink_bg is not None:
            self._ink_bg.setActive(False)
            self._ink_bg.hide()

    def _apply_ink_mode(self) -> None:
        self._active_skin_kind = "ink"
        self._remove_acrylic()
        self._hide_image_bg()
        background = self._ensure_ink_bg(self.app.state.settings.inkTheme)
        background.setActive(True)
        background.setGeometry(0, 0, self.width(), self.height())
        background.show()
        background.lower()
        if self.container:
            self.container.raise_()
        if self.isVisible():
            background.start()
        set_rounded_corners(int(self.winId()), True)
        self.apply_text_colors()

    def _apply_image_mode(self) -> None:
        # Static image surface: _image_bg paints the cover-scaled image below the
        # (capture-excluded) content layer, and DWM rounds the window corners.
        self._active_skin_kind = "image"
        self._remove_acrylic()
        self._hide_ink_bg()
        path = getattr(self.skin, "image_path", None)
        pixmap = load_skin_pixmap(path) if path is not None else None
        self._image_pixmap = pixmap
        self._image_luminance = image_mean_luminance(pixmap) if pixmap is not None else 1.0
        image_bg = self._ensure_image_bg()
        image_bg.set_image(pixmap)
        image_bg.setGeometry(0, 0, self.width(), self.height())
        image_bg.show()
        image_bg.lower()  # beneath the content layer; container is raised back on top below
        if self.container:
            self.container.raise_()
        set_rounded_corners(int(self.winId()), True)
        self.apply_text_colors()

    def _apply_acrylic_effect(self) -> None:
        settings = self.app.state.settings
        tint = qcolor(getattr(self.skin, "acrylic_tint", None) or settings.windowTint, "#F2F4F7")
        gradient = f"{tint.red():02X}{tint.green():02X}{tint.blue():02X}{ACRYLIC_TINT_ALPHA:02X}"
        if self._acrylic_applied and gradient == self._acrylic_signature:
            return  # avoid re-issuing the composition attribute on every slider tick (flicker)
        hwnd = int(self.winId())
        self._window_effect.setAcrylicEffect(hwnd, gradient, enableShadow=True)
        set_rounded_corners(hwnd, True)
        self._acrylic_applied = True
        self._acrylic_signature = gradient

    def _remove_acrylic(self) -> None:
        if not self._acrylic_applied:
            return
        try:
            hwnd = int(self.winId())
            self._window_effect.removeBackgroundEffect(hwnd)
            set_rounded_corners(hwnd, False)
        except Exception:
            pass
        self._acrylic_applied = False
        self._acrylic_signature = None

    def apply_window_layer(self) -> None:
        # SetWindowPos/Z-order churn on every apply_settings call makes the window flash;
        # the tool-window style, parent detach, and topmost flag are sticky, so once is enough.
        # The topmost flag itself follows 允许被遮盖 (allowCover): False pins the window on top,
        # True lets other windows cover it. Toggling the setting re-runs just the flag.
        if not self.isVisible():
            return
        hwnd = int(self.winId())
        if not self._window_layer_applied:
            apply_tool_window(hwnd)
            detach_from_parent(hwnd)
            self._window_layer_applied = True
        set_topmost(hwnd, not self.app.state.settings.allowCover)

    @staticmethod
    def _todo_sort_key(item: TodoItem) -> tuple:
        # Urgent items stay pinned; within each group the user's drag order is authoritative.
        return (not item.urgent, item.order, item.createdAt)

    def _todo_rows_in_layout(self) -> list[TodoRow]:
        rows: list[TodoRow] = []
        for index in range(self.list_layout.count()):
            widget = self.list_layout.itemAt(index).widget()
            if isinstance(widget, TodoRow):
                rows.append(widget)
        return rows

    def begin_todo_reorder(self, row: TodoRow) -> None:
        if row not in self._todo_rows_in_layout():
            return
        self._reorder_row = row
        row.raise_()

    def move_todo_reorder(self, row: TodoRow, global_pos: QPoint) -> None:
        if self._reorder_row is not row:
            return
        rows = self._todo_rows_in_layout()
        if row not in rows:
            return
        cursor_y = self.list_widget.mapFromGlobal(global_pos).y()
        others = [candidate for candidate in rows if candidate is not row]
        target_index = sum(cursor_y >= candidate.geometry().center().y() for candidate in others)
        if target_index == rows.index(row):
            return
        # target_index counts TodoRows only, but insertWidget is layout-absolute. Offset by any
        # leading non-TodoRow widgets (the calendar header) so a drop lands at the visual slot
        # and a todo can never be inserted above the header.
        leading = 0
        for index in range(self.list_layout.count()):
            if isinstance(self.list_layout.itemAt(index).widget(), TodoRow):
                break
            leading += 1
        self.list_layout.removeWidget(row)
        self.list_layout.insertWidget(target_index + leading, row)
        self.list_layout.activate()
        row.raise_()

    def finish_todo_reorder(self, row: TodoRow) -> None:
        if self._reorder_row is not row:
            return
        self._reorder_row = None
        # Assign one monotonic sequence to the visual list. refresh() immediately re-pins urgent
        # rows while preserving the resulting manual order inside each urgent/non-urgent group.
        for order, ordered_row in enumerate(self._todo_rows_in_layout()):
            ordered_row.todo.order = order
        self.app.save()
        self.refresh()

    def refresh(self) -> None:
        self.close_details_preview()
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._rows.clear()
        self._event_rows.clear()
        self._calendar_header = None

        active = sorted(self.app.state.todos, key=self._todo_sort_key)
        events = self._visible_calendar_events()
        self.scroll.setVisible(bool(active) or bool(events))
        self.empty.setVisible(not active and not events)

        for todo in active:
            row = TodoRow(todo, self.app.state.settings, self)
            self._rows[todo.id] = row
            self.list_layout.addWidget(row)

        if events:
            self._calendar_header = self._make_calendar_header()
            self.list_layout.addWidget(self._calendar_header)
            done = set(self.app.state.calendarDoneKeys)
            for event in events:
                row = CalendarRow(event, event.key in done, self)
                self._event_rows[event.key] = row
                self.list_layout.addWidget(row)

        self.list_layout.addStretch()
        self._resize_for_content(active, events)
        self.apply_text_colors()
        controller = getattr(self.app, "floating", None)
        if controller is not None:
            controller.update_status()

    def _visible_calendar_events(self) -> list[CalendarEvent]:
        # Synced events are read-only and never archive to history (they would just re-sync),
        # so unlike todos they ignore completeBehavior: checking one only dims + strikes it
        # through in place and it stays visible until it drops out of the sync window.
        # Only events of checked feeds show; unchecked feeds keep their cache hidden.
        settings = self.app.state.settings
        if not settings.calendarEnabled:
            return []
        visible = {feed.id for feed in settings.active_calendar_feeds()}
        return [event for event in self.app.state.calendarEvents if event.feedId in visible]

    def _make_calendar_header(self) -> QLabel:
        header = QLabel("日程")
        header.setFixedHeight(CALENDAR_HEADER_HEIGHT)
        color = self._normal_text_color()
        header.setStyleSheet(
            f"{FONT_STACK_QSS} color: {css_rgba(color, 0.7)}; font-size: 11pt; font-weight: 600; padding-left: 6px;"
        )
        return header

    def toggle_calendar_event(self, key: str, checked: bool) -> None:
        self.app.calendar.toggle_event_done(key, checked)

    def toggle_expanded(self) -> None:
        self._expanded = not self._expanded
        # Expanded auto-fits everything; treat the toggle as "back to automatic sizing" so a
        # previous manual drag doesn't fight the fit-to-content model.
        self.app.state.window.userSized = False
        self.expand_button.setText("▴" if self._expanded else "▾")
        self.expand_button.setToolTip("收起" if self._expanded else "展开全部")
        self.refresh()

    def _scroll_overflowing(self) -> bool:
        bar = self.scroll.verticalScrollBar()
        return bar is not None and bar.maximum() > 0

    def _on_global_wheel(self, gx: int, gy: int, delta: int) -> bool:
        # Invoked from the low-level mouse hook on the GUI thread's message pump. Scroll only
        # when the list is visible, overflowing, and the cursor is over the scroll area; else
        # return False so the wheel passes through to whatever is underneath.
        if not self.isVisible() or not self.scroll.isVisible() or not self._scroll_overflowing():
            return False
        local = self.mapFromGlobal(QPoint(gx, gy))
        if not self._rect_for(self.scroll).contains(local):
            return False
        bar = self.scroll.verticalScrollBar()
        if bar is None:
            return False
        bar.setValue(bar.value() - round(delta / 120.0 * 60))
        return True

    def _acrylic_text_color(self) -> QColor:
        # The frost tint dominates the surface, so contrast is deterministic: pick the soft
        # dark or soft light text by the tint's luminance. No sampling, no neon, no flicker.
        tint = qcolor(self.app.state.settings.windowTint, "#F2F4F7")
        return best_contrast_color(tint, [ACRYLIC_TEXT_DARK, ACRYLIC_TEXT_LIGHT])

    def _image_text_color(self) -> QColor:
        # Image mode has no live capture to sample, so contrast is deterministic: soft dark text
        # on a bright image, soft light text on a dark one (reusing the acrylic text palette).
        dark, light = qcolor(ACRYLIC_TEXT_DARK), qcolor(ACRYLIC_TEXT_LIGHT)
        return dark if self._image_luminance > 0.55 else light

    def _normal_text_color(self) -> QColor:
        settings = self.app.state.settings
        override = getattr(self.skin, "text_override", None)
        if override is not None:
            return qcolor(override)
        if settings.skin.startswith("image:"):
            # Image surfaces honor a manual color choice; otherwise pick by image luminance.
            if settings.fontColorMode == "manual":
                return qcolor(settings.todoTextColor)
            return self._image_text_color()
        return self._acrylic_text_color()  # frost: deterministic by tint luminance

    def text_color_for(self, todo: TodoItem) -> QColor:
        if todo.urgent:
            return qcolor(self.app.state.settings.urgentTextColor, "#FF0000")
        return self._normal_text_color()

    def text_needs_halo(self) -> bool:
        # Every surface provides a stable readability base; the swirl adds its own calm veil.
        # Kept as a method because TodoRow/CalendarRow constructors call it.
        return False

    def apply_text_colors(self) -> None:
        for row in self._rows.values():
            row.apply_text_style(self.text_color_for(row.todo), self.text_needs_halo())
        normal = self._normal_text_color()
        for row in self._event_rows.values():
            row.apply_text_style(normal, self.text_needs_halo())
        if self._calendar_header is not None:
            self._calendar_header.setStyleSheet(
                f"{FONT_STACK_QSS} color: {css_rgba(normal, 0.7)}; font-size: 11pt; font-weight: 600; padding-left: 6px;"
            )
        # `normal` already resolves to the manual/acrylic/image color, so it is the right base
        # for the empty-state label in every skin and font mode.
        self.empty.setStyleSheet(f"{FONT_STACK_QSS} color: {css_rgba(normal, 0.58)}; font-size: {EMPTY_HINT_FONT_PX}px;")

    def _resize_for_content(self, active: list[TodoItem], events: list[CalendarEvent] | None = None) -> None:
        events = events or []
        screen = QApplication.primaryScreen().availableGeometry()
        show_todo_ddl = any(todo.ddl for todo in active)
        # The time column is shared by todo DDLs and event times; show it (and reserve width)
        # whenever either group needs it, sizing to the widest string across both for alignment.
        column_active = show_todo_ddl or bool(events)
        ddl_width = self._time_column_width(active, events, self._expanded) if column_active else 0
        ddl_reserve = (ddl_width + DDL_SEP_WIDTH + DDL_COL_GAPS) if column_active else 0
        scrollbar_reserve = 0 if self._expanded else SCROLLBAR_LAYOUT_RESERVE
        trailing_reserve = ddl_reserve + scrollbar_reserve
        auto_width = self._adaptive_width(active, events, screen, trailing_reserve)
        has_subs = any(todo.subtasks for todo in active)
        text_width = self._text_width_for_window(auto_width, trailing_reserve, has_subs)
        content_height = sum(
            self._measure_row_height(todo.text, text_width, todo.location) + todo_extra_height(todo)
            for todo in active
        )
        if events:
            # Calendar rows render "📅 {summary}", which wraps (and grows taller than ROW_HEIGHT)
            # for long titles — measure them like todos so the window height isn't underestimated
            # (which previously left expanded mode hiding the scrollbar yet still clipping rows).
            content_height += CALENDAR_HEADER_HEIGHT
            content_height += sum(self._measure_row_height(f"📅 {event.summary}", text_width, event.location) for event in events)
        content_height = max(content_height, ROW_HEIGHT)
        # The scrollable list adds top/bottom breathing room around the rows; budget it here so
        # the window grows to keep the first/last rows fully visible instead of squeezing them.
        content_height += 2 * LIST_EDGE_PAD
        # Window height = top block + rows + corner margin (scale is 1.0 for both static skins,
        # kept here so the formula still reads generally).
        scale = self.skin.geometry_scale
        corner = self.skin.corner_margin
        needed = MEMO_TOP_BLOCK + content_height + 2 * corner
        wanted = max(MIN_HEIGHT, math.ceil(needed / scale))
        screen_cap = max(MIN_HEIGHT, screen.height() - 64)
        width_cap = max(MIN_WIDTH, screen.width() - 64)
        # min/max range (not setFixed*) so the native corner/edge drag-resize can change the
        # size; in auto mode the computed size is applied explicitly below.
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
        self.setMaximumSize(width_cap, screen_cap)
        # A drag-resize pins a manual size (window.userSized): respect it in collapsed mode and
        # just reflow the content to it. 展开全部/收起 clears the flag back to auto-fit.
        manual = self.app.state.window.userSized and not self._expanded
        if manual:
            width = min(max(self.app.state.window.width, MIN_WIDTH), width_cap)
            height = min(max(self.app.state.window.height, MIN_HEIGHT), screen_cap)
            # A manual width can be narrower than the natural DDL column: clamp the column so
            # the title keeps ROW_MIN_TEXT and every trailing widget (❗ / ✎) stays visible.
            ddl_reserve = min(ddl_reserve, max(0, width - row_fixed_chrome(0, has_subs) - ROW_MIN_TEXT))
            ddl_width = max(0, ddl_reserve - DDL_SEP_WIDTH - DDL_COL_GAPS) if column_active else 0
            trailing_reserve = ddl_reserve + scrollbar_reserve
            text_width = self._text_width_for_window(width, trailing_reserve, has_subs)
            self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        elif self._expanded:
            # Grow to fit everything; only the physical screen limits us. Hide the scrollbar
            # when it all fits, but fall back to AsNeeded if content still exceeds the screen.
            width = auto_width
            height = min(wanted, screen_cap)
            fits = wanted <= screen_cap
            self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff if fits else Qt.ScrollBarAsNeeded)
            text_width = self._text_width_for_window(width, trailing_reserve, has_subs)
        else:
            width = auto_width
            height = min(wanted, int(screen.height() * MAX_HEIGHT_RATIO), screen_cap)
            self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            text_width = self._text_width_for_window(width, trailing_reserve, has_subs)
        # Apply in every branch: the manual size also has to be enforced at startup (the window
        # is built at its constructor size long before the saved geometry is consulted).
        if (width, height) != (self.width(), self.height()):
            self.resize(width, height)

        # Inset the content. Both skins fill the window (geometry_scale = 1.0 → vertical/horizontal
        # padding are 0), so this collapses to OUTER_X horizontally and the corner margin vertically.
        pad_y = self.skin.vertical_padding(height)
        pad_x = max(OUTER_X, self.skin.horizontal_padding(width))
        self.layout.setContentsMargins(pad_x, pad_y + corner, pad_x, pad_y + corner)

        for row in self._rows.values():
            row.apply_text_width(text_width, show_todo_ddl, ddl_width)
        for row in self._event_rows.values():
            row.apply_text_width(text_width, True, ddl_width)

        self._keep_inside_screen(screen)
        self.app.state.window.width = width
        self.app.state.window.height = height
        controller = getattr(self.app, "floating", None)
        if controller is not None:
            controller.reposition_panel()
        if self._dock_edge is not None:
            # Content resized while docked: re-pin to the (recomputed) dock position so the peek
            # strip and slide geometry stay correct for the new size.
            self._reposition_dock()

    def _adaptive_width(self, active: list[TodoItem], events: list[CalendarEvent], screen: QRect, ddl_reserve: int = 0) -> int:
        minimum = max(MIN_WIDTH, INK_MIN_WIDTH if self.skin.kind == "ink" else 0)
        if not active and not events:
            return minimum
        metrics = QFontMetrics(mixed_font(12))
        text_widths = [metrics.horizontalAdvance(todo.text) for todo in active]
        text_widths += [metrics.horizontalAdvance(f"📅 {event.summary}") for event in events]
        longest = max(text_widths) if text_widths else 0
        # Same chrome the title width must subtract (incl. the subtask ▾/badge worst case) so
        # auto-size always leaves room for every trailing widget.
        chrome = row_fixed_chrome(ddl_reserve, has_subtasks=True)
        max_width = min(MAX_WIDTH, int(screen.width() * MAX_WIDTH_RATIO), screen.width() - 64)
        return max(minimum, min(max_width, longest + chrome))

    def _text_width_for_window(self, width: int, ddl_reserve: int = 0, has_subtasks: bool = False) -> int:
        # Must use the same chrome as _adaptive_width (row_fixed_chrome) or the title label is
        # pinned wider than the row can hold and pushes the trailing ❗ / ✎ buttons off the
        # right edge, where they clip out of view.
        return max(ROW_MIN_TEXT, width - row_fixed_chrome(ddl_reserve, has_subtasks))

    def _time_column_width(self, active: list[TodoItem], events: list[CalendarEvent], expanded: bool = False) -> int:
        # Width that fits the widest deadline/event-time text in this view (so they show in
        # full), clamped to [DDL_COL_MIN, cap]. Collapsed elides past DDL_COL_MAX; expanded
        # lifts the cap so nothing is truncated.
        metrics = QFontMetrics(mixed_font(11))
        candidates = [todo.ddl.strip() for todo in active if todo.ddl.strip()]
        candidates += [format_event_time(event) for event in events]
        longest = max((metrics.horizontalAdvance(text) for text in candidates), default=0)
        cap = DDL_COL_EXPANDED_MAX if expanded else DDL_COL_MAX
        return max(DDL_COL_MIN, min(cap, longest + DDL_COL_PAD))

    def _measure_row_height(self, text: str, text_width: int, location: str = "") -> int:
        # Mirror TodoRow/CalendarRow.apply_text_width so the window height pre-calc matches the
        # rows' actual heights (incl. the optional 📍 location second line). Subtask/recur extras
        # are added by the caller via todo_extra_height.
        height = wrapped_text_height(text, text_width) + (location_line_height() if location.strip() else 0)
        return max(ROW_HEIGHT, height + 18)

    def _keep_inside_screen(self, screen: QRect) -> None:
        if not self.isVisible():
            return
        if self._dock_edge is not None:
            return  # docked positions intentionally sit at / past the screen edge
        margin = 12
        x = min(max(self.x(), screen.left() + margin), screen.right() - self.width() - margin)
        y = min(max(self.y(), screen.top() + margin), screen.bottom() - self.height() - margin)
        if x != self.x() or y != self.y():
            self.move(x, y)

    def _popup_position(self, popup_width: int, anchor: QPoint, popup_height: int = 132) -> QPoint:
        screen = QApplication.primaryScreen().availableGeometry()
        x = min(max(anchor.x(), screen.left() + 12), screen.right() - popup_width - 12)
        y = anchor.y()
        if y + popup_height > screen.bottom() - 12:
            y = anchor.y() - popup_height - 12
        y = min(max(y, screen.top() + 12), screen.bottom() - popup_height - 12)
        return QPoint(x, y)

    def show_add_popup(self) -> None:
        popup_width = max(420, min(600, self.width() + 56))
        anchor = QPoint(self.x() + (self.width() - popup_width) // 2, self.y() + self.height() + 10)
        self.add_popup.open_add(self._popup_position(popup_width, anchor), popup_width)

    def add_todo(
        self,
        text: str,
        location: str = "",
        ddl: str = "",
        details: str = "",
        subtasks: list[SubTask] | None = None,
        recur: str = "none",
    ) -> None:
        next_order = max([todo.order for todo in self.app.state.todos] + [0]) + 1
        self.app.state.todos.append(
            TodoItem(
                id=str(uuid4()), text=text, ddl=ddl, location=location, order=next_order,
                details=details or "", subtasks=list(subtasks or []), recur=parse_recur(recur) or "none",
            )
        )
        self.app.save()
        self.refresh()

    def edit_todo(self, todo_id: str) -> None:
        todo = next((item for item in self.app.state.todos if item.id == todo_id), None)
        if todo is None:
            return
        self.close_details_preview()
        popup_width = max(420, min(600, self.width() + 56))
        row = self._rows.get(todo_id)
        if row is not None:
            anchor = row.edit_btn.mapToGlobal(QPoint(0, row.edit_btn.height() + 6))
        else:
            anchor = QPoint(self.x(), self.y() + self.height() + 10)
        self.add_popup.open_edit(
            todo,
            self._popup_position(popup_width, anchor), popup_width,
        )

    def update_todo(
        self,
        todo_id: str,
        text: str,
        location: str,
        ddl: str,
        details: str | None = None,
        subtasks: list[SubTask] | None = None,
        recur: str | None = None,
    ) -> None:
        todo = next((item for item in self.app.state.todos if item.id == todo_id), None)
        if todo is None:
            return
        # details/subtasks/recur are optional: None leaves the stored value untouched, so the
        # plain three-field call keeps its old no-op-if-unchanged semantics.
        if details is None and subtasks is None and recur is None:
            if (todo.text, todo.location, todo.ddl) == (text, location, ddl):
                return
        todo.text = text
        todo.location = location
        todo.ddl = ddl
        if details is not None:
            todo.details = details
        if subtasks is not None:
            todo.subtasks = list(subtasks)
        if recur is not None:
            todo.recur = parse_recur(recur) or "none"
            if todo.recur == "none":
                todo.recurAnchor = None
        self.app.save()
        self.refresh()

    def toggle_urgent(self, todo_id: str) -> None:
        for todo in self.app.state.todos:
            if todo.id == todo_id:
                todo.urgent = not todo.urgent
                break
        self.app.save()
        self.refresh()

    def complete_todo(self, todo_id: str, checked: bool, row: TodoRow) -> None:
        todo = next((item for item in self.app.state.todos if item.id == todo_id), None)
        if not todo:
            return
        self.close_details_preview()
        if not checked:
            # Unchecking the parent leaves subtask state untouched.
            todo.done = False
            todo.completedAt = None
            self.app.save()
            self.refresh()
            return
        if checked and todo.subtasks:
            # 双向半自动: completing the parent marks every subtask done (no completedAt on
            # SubTask — done is the only record).
            for sub in todo.subtasks:
                sub.done = True

        now = datetime.now()
        recur_token = parse_recur(todo.recur)
        if recur_token not in (None, "none"):
            # Snapshot the completed occurrence (with its OLD ddl) into history, then roll the
            # live item forward. Both dim and archive modes regenerate; dim just means the
            # regenerated item stays dimmed-in-place rather than animating out.
            snapshot = replace(
                todo,
                id=str(uuid4()),
                ddl=todo.ddl,
                done=True,
                completedAt=utc_now(),
                subtasks=[replace(sub) for sub in todo.subtasks],
            )
            if apply_recurrence_on_complete(todo, now):
                self.app.state.history.append(snapshot)
                self.app.save_later()
                self.refresh()
                self.app.history_window.refresh()
                return
            # Unparseable ddl / no next occurrence: fall through to the normal completion path.
            todo.done = True
            todo.completedAt = utc_now()

        if self.app.state.settings.completeBehavior == "dim":
            todo.done = True
            todo.completedAt = utc_now()
            self.app.save()
            self.refresh()
            return

        effect = QGraphicsOpacityEffect(row)
        row.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", row)
        anim.setDuration(180)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        anim.finished.connect(lambda: self.app.archive_todo(todo_id))
        anim.start(QPropertyAnimation.DeleteWhenStopped)

    def apply_ink_theme(self, theme: "InkTheme | None") -> None:
        self._acrylic_signature = None
        for button in (self.add_button, self.hide_button, self.expand_button):
            button.apply_ink_theme(theme)
        self.apply_settings(refresh_rows=True)
        self.add_popup.apply_ink_theme(theme)
        self.details_popup.apply_ink_theme(theme)


class LiquidMemoApp:
    def __init__(self) -> None:
        self.qt = QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)
        if sys.platform == "win32":
            import ctypes
            # Own taskbar identity: without this, dev runs under pythonw.exe group under the
            # Python AppUserModelID and taskbar buttons keep the Python icon despite windowIcon.
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CEQ151.LiquidMemoWidget")
        setTheme(Theme.LIGHT)
        self.qt.setFont(mixed_font(10))
        self.qt.setWindowIcon(tray_icon())
        # Tooltips are rendered as qfluentwidgets bubbles via install_tooltip(); a native
        # QToolTip here inherits the owning widget's (white) text color and styling it through
        # this app-level `*`/QToolTip rule is unreliable, so we don't try.
        self.qt.setStyleSheet(f"* {{ {FONT_STACK_QSS} }}")
        self.store = StateStore()
        self.state = self.store.load()
        # Set the screen-capture policy before any window is created, so each window's showEvent
        # applies the right affinity from the first show.
        set_capture_exclusion(not self.state.settings.allowScreenshot)
        self.save_timer = QTimer()
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.save)
        self.ink = InkThemeController(self)
        self.window = MemoWindow(self)
        self.settings_window = SettingsWindow(self)
        self.history_window = HistoryWindow(self)
        self.calendar = CalendarManager(self)
        self.notifier = NotificationManager(self)
        self.updater = UpdateManager(self)
        self.floating = FloatingModeController(self)
        self.ink.bind_ui()
        self.tray_menu: RoundMenu | None = None
        self.tray = QSystemTrayIcon(tray_icon())
        self.tray.setToolTip("桌面备忘")
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()
        self.qt.aboutToQuit.connect(self.shutdown)

    def run(self) -> int:
        # Re-claim the launch-at-login entry for this exe if a different build (e.g. the
        # portable copy, before this one was installed) had left it pointing elsewhere.
        reconcile_startup()
        # The mode controller decides whether startup shows the memo itself or only the launcher.
        self.floating.start()
        # Kick off the first calendar sync once the event loop is about to run.
        QTimer.singleShot(0, self.calendar.start)
        # Start the reminder scan after the window/state have settled.
        QTimer.singleShot(2000, self.notifier.start)
        # Changelog-after-update + delayed silent update check, after the UI settles.
        QTimer.singleShot(1500, self.updater.on_startup)
        return self.qt.exec()

    def save_later(self) -> None:
        self.save_timer.start(350)

    def save(self) -> None:
        self.store.save(self.state)

    def apply_capture_policy(self) -> None:
        """Update the process-wide screen-capture policy from settings and re-apply it to the
        windows that opt out of capture (memo / launcher / note dialog) that may already be shown.
        Hidden windows pick it up from their showEvent."""
        set_capture_exclusion(not self.state.settings.allowScreenshot)
        self.window.protect_content_layer()
        floating = getattr(self, "floating", None)
        launcher = floating.launcher if floating is not None else None
        if launcher is not None and launcher.isVisible():
            protect_window_from_capture(int(launcher.winId()))

    def archive_todo(self, todo_id: str) -> None:
        for index, todo in enumerate(self.state.todos):
            if todo.id == todo_id:
                todo.done = True
                todo.completedAt = utc_now()
                self.state.history.append(todo)
                self.state.todos.pop(index)
                break
        self.save()
        self.window.refresh()
        self.history_window.refresh()

    def restore_from_history(self, todo_id: str) -> None:
        for index, todo in enumerate(self.state.history):
            if todo.id == todo_id:
                todo.done = False
                todo.completedAt = None
                todo.order = max([item.order for item in self.state.todos] + [0]) + 1
                self.state.todos.append(todo)
                self.state.history.pop(index)
                break
        self.save()
        self.window.refresh()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self.toggle_window()
        elif reason == QSystemTrayIcon.Context:
            self.show_tray_menu()

    def show_tray_menu(self) -> None:
        pos = QCursor.pos()
        # qfluentwidgets' RoundMenu gives the Fluent look (rounded translucent card, hover
        # animation, proper icon/text metrics) instead of a hand-styled QMenu. No parent: it is
        # a top-level popup, and a deleted parent would take its view down with it.
        menu = RoundMenu()
        menu.setItemHeight(54)
        # The library's menu.qss hardcodes `font: 14px` on the view, which beats setFont — set
        # the app font through the same selector instead. Icons scale via the view's iconSize.
        menu.view.setFont(mixed_font_px(22))
        font_qss = f"MenuActionListWidget {{ font: 22px 'Times New Roman','Microsoft YaHei','Segoe UI Emoji'; }}"
        ink = getattr(self, "ink", None)
        theme = ink.theme if getattr(ink, "active", False) else None
        if theme:
            font_qss += f" MenuActionListWidget {{ background-color: {theme.popup_bg}; border-radius: 8px; }}"
        menu.view.setStyleSheet(font_qss)
        menu.view.setIconSize(QSize(24, 24))
        menu.view.setMinimumWidth(272)
        menu.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._add_tray_action(menu, FluentIcon.SETTING, "设置", self.show_settings)
        self._add_tray_action(menu, FluentIcon.HISTORY, "历史记录", self.show_history)
        menu.addSeparator()
        surfaces_visible = self.floating.surfaces_visible()
        label = "隐藏悬浮窗" if surfaces_visible else "显示悬浮窗"
        if self.state.settings.windowMode != "floatingLauncher":
            label = "隐藏窗口" if surfaces_visible else "显示窗口"
        icon = FluentIcon.HIDE if surfaces_visible else FluentIcon.VIEW
        self._add_tray_action(menu, icon, label, self.toggle_window)
        self._add_tray_action(menu, FluentIcon.POWER_BUTTON, "退出", self.quit)
        self.tray_menu = menu
        # The tray icon lives at the bottom-right of the screen, so dropping the menu downward
        # from the cursor pushed it off the bottom edge / too low. Instead anchor the menu's
        # bottom-right corner near the cursor so it opens up-and-to-the-left, and clamp it to
        # the screen work area so it is never clipped.
        menu.adjustSize()
        size = menu.view.size()
        screen = self.qt.screenAt(pos) or self.qt.primaryScreen()
        area = screen.availableGeometry()
        x = pos.x() - size.width() - 6
        y = pos.y() - size.height()
        x = max(area.left() + 4, min(x, area.right() - size.width() - 4))
        y = max(area.top() + 4, min(y, area.bottom() - size.height() - 4))
        menu.exec(QPoint(x, y), aniType=MenuAnimationType.FADE_IN_DROP_DOWN)

    def _add_tray_action(self, menu: "RoundMenu", icon: FluentIcon, text: str, callback) -> None:
        action = Action(icon, text, menu)
        action.triggered.connect(callback)
        menu.addAction(action)

    def toggle_window(self) -> None:
        self.floating.toggle_surfaces()

    def hide_memo_window(self) -> None:
        """The memo's minus button collapses to the launcher in floating mode."""
        if self.state.settings.windowMode == "floatingLauncher":
            self.floating.collapse_panel()
        else:
            self.window.hide()

    def show_settings(self) -> None:
        self.settings_window.sync_from_state()
        self._center_widget(self.settings_window)
        self.settings_window.show()
        self.settings_window.activateWindow()
        self.settings_window.raise_()

    def show_history(self) -> None:
        self.history_window.refresh()
        self._center_widget(self.history_window)
        self.history_window.show()
        self.history_window.activateWindow()
        self.history_window.raise_()

    def _center_widget(self, widget: QWidget) -> None:
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.left() + (screen.width() - widget.width()) // 2
        y = screen.top() + (screen.height() - widget.height()) // 2
        widget.move(x, y)

    def quit(self) -> None:
        self.save()
        self.qt.quit()

    def quit_for_update(self) -> None:
        """Make the visible app disappear promptly before the detached updater takes over.

        Active QRunnables cannot be cancelled safely, so the helper still has a same-executable
        watchdog for a worker that outlives Qt. Clearing queued work and stopping every manager
        keeps the normal path graceful and avoids waiting on work that has not started yet.
        """
        self.save_timer.stop()
        self.save()
        for manager in (self.calendar, self.notifier, self.ink, self.floating):
            try:
                manager.stop()
            except Exception:
                pass
        QThreadPool.globalInstance().clear()
        try:
            self.tray.hide()
        except Exception:
            pass
        for widget in self.qt.topLevelWidgets():
            widget.hide()
        self.qt.quit()

    def shutdown(self) -> None:
        self.save()
        try:
            self.calendar.stop()
            self.notifier.stop()
            self.ink.stop()
        except Exception:
            pass
        try:
            self.floating.stop()
        except Exception:
            pass
        try:
            self.window._wheel_hook.uninstall()
        except Exception:
            pass
        try:
            self.window.cleanup()
        except Exception:
            pass


if __name__ == "__main__":
    app = LiquidMemoApp()
    raise SystemExit(app.run())
