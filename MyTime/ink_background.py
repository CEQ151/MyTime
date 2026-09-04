"""Background surface for the 灵动水墨 (ink-wash) skin.

Prefers the GPU ink-wash (``experimental_fluid.FluidGLWidget``) and falls back to
the QPainter swirl (``ink_swirl.SwirlPainterFallback``) when OpenGL / PyOpenGL
is unavailable — e.g. a PyInstaller build that does not bundle PyOpenGL yet, or a
machine without a usable GL 3.3 context.

Both surfaces expose the same ``start`` / ``stop`` / ``setActive`` / ``cleanup`` /
``setGeometry`` lifecycle that ``MemoWindow`` drives, so ``app.py`` treats whatever
``make_ink_background`` returns interchangeably. The window itself supplies the
smooth rounded corners via DWM (``set_rounded_corners``), so this layer never masks.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QWidget

from ink_swirl import SwirlPainterFallback, swirl_tokens

_EXPERIMENT_DIR = Path(__file__).parent / "experimental_fluid"

# Ink-wash palette (宣纸底色, 中墨, 浓墨) per 水墨主题, so the GPU background follows the
# chosen theme palette. "qinghua" matches FluidConfig's blue-on-rice defaults. Keyed by the same
# theme keys as ink_theme.INK_THEMES; unknown keys fall back to qinghua.
_INK_PALETTE_BY_THEME = {
    "qinghua": ("#eae5c8", "#3d7ab3", "#0154a7"),
    "warm": ("#efe7d4", "#bf7d5e", "#8c3b2f"),
    "blush": ("#f6ebe9", "#c96a86", "#8e2f54"),
}


def _ink_palette(theme_key: str) -> tuple[str, str, str]:
    return _INK_PALETTE_BY_THEME.get(theme_key, _INK_PALETTE_BY_THEME["qinghua"])


def _configure_gl_format() -> None:
    # Request a 3.3 Core context for the ink-wash shaders. Setting the default
    # format affects GL contexts created afterwards (our widget is created on
    # demand when the ink skin activates), which is exactly what we need.
    from PySide6.QtGui import QSurfaceFormat

    fmt = QSurfaceFormat()
    fmt.setRenderableType(QSurfaceFormat.OpenGL)
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    fmt.setSwapBehavior(QSurfaceFormat.DoubleBuffer)
    QSurfaceFormat.setDefaultFormat(fmt)


def _make_gl_background(parent: QWidget, theme_key: str) -> QWidget:
    """Create the GPU ink-wash widget, or raise if OpenGL is not importable/usable."""
    from experimental_fluid.fluid_gl_widget import FluidGLWidget
    from experimental_fluid.fluid_config import FluidConfig

    cfg_path = _EXPERIMENT_DIR / "inkwash.json"
    cfg = FluidConfig.from_tuner_json(cfg_path) if cfg_path.exists() else FluidConfig()
    # Theme overrides colour only (motion/quality from the tuner JSON, if any, is preserved).
    cfg.paper_hex, cfg.ink_mid_hex, cfg.ink_deep_hex = _ink_palette(theme_key)

    class _InkBackground(FluidGLWidget):
        """Adapts FluidGLWidget to MemoWindow's swirl-background lifecycle."""

        def setActive(self, active: bool) -> None:
            if active:
                self.show()
            else:
                self.stop()
                self.hide()

        def start(self) -> None:
            if not self._frame_timer.isActive():
                self._frame_timer.start()

        def stop(self) -> None:
            self._frame_timer.stop()

        def cleanup(self) -> None:
            self.stop()

        def set_theme(self, theme_key: str) -> None:
            # _render uploads cfg.paper/ink_mid/ink_deep as uniforms every frame, so recolour in
            # place — no GL context rebuild — when the ink theme changes.
            self.cfg.paper_hex, self.cfg.ink_mid_hex, self.cfg.ink_deep_hex = _ink_palette(theme_key)
            self.update()

    _configure_gl_format()
    widget = _InkBackground(cfg, parent)
    # Behind the click-through content layer the widget gets no Qt mouse events,
    # so stir ink from the global cursor position instead.
    widget.set_global_cursor_tracking(True)
    return widget


def make_ink_background(parent: QWidget, theme_key: str = "qinghua") -> QWidget:
    """Return the ink-wash background: GPU fluid if possible, else the QPainter swirl.

    `theme_key` selects the 水墨主题 palette so the background colour follows the chosen ink theme.
    Any failure to build the GL surface (no PyOpenGL, no GL 3.3, import error) falls back to the
    always-available QPainter swirl so the ink skin never breaks.
    """
    try:
        return _make_gl_background(parent, theme_key)
    except Exception:
        return SwirlPainterFallback(parent, tokens=swirl_tokens(theme_key))
