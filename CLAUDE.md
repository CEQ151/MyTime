# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Windows 11 desktop memo/todo widget rendered as a translucent surface floating on the desktop.
The window is a lightweight **DWM acrylic frost** (the default "磨砂玻璃" skin), an animated
GPU/CPU **ink-wash surface** ("灵动水墨"), or a static user-supplied **image background**, with a
Qt content layer (todo rows, buttons) on top. Windows-only (Win32 + DWM); will not run or build
on other platforms.

> History: earlier versions also offered a real-time D3D11 "液态玻璃" (liquid glass) skin that
> screen-captured the desktop behind the window and refracted it through GPU effects. That skin
> was removed (it was buggy and heavy); the vendored `WindowsLiquidGlass` engine, the capture /
> effect pipeline, and the `numpy` dependency are all gone. Don't reintroduce a screen-capture
> render path.

UI strings are Chinese. Code/identifiers are English.

## Commands

```powershell
# Run from source (preferred entry point — pythonw = no console window)
python -m pip install -r .\MyTime\requirements.txt
pythonw .\RunMyTime.pyw

# Headless regression suite (Qt uses the offscreen platform in tests/conftest.py)
py -3.13 -m pytest .\tests -q

# Build a PyInstaller bundle into dist\MyTime\
.\Build.ps1

# Package portable zip + Inno Setup installer (needs Inno Setup 6 on PATH or via -InnoSetupPath)
.\Package.ps1 -Version 0.0.1
.\Package.ps1 -Version 0.0.1 -SkipInstaller   # zip only
```

**PyInstaller pitfall:** app sources under `MyTime/` ship via `--add-data` and are
imported only at runtime, so PyInstaller never analyzes their imports. Any new stdlib or
third-party module imported there must also be added to `Build.ps1` as `--hidden-import`
(this shipped a launch crash once: `xml.etree` was missing). Smoke-test
`dist\MyTime\MyTime.exe` after changing imports.

The regression suite lives under `tests/`; there is no linter configured. Releases are produced by
`.github/workflows/release.yml`, triggered by pushing a `v*` tag. **Before tagging a release:**
bump `APP_VERSION` in `MyTime/version.py` and add a `## vX.Y.Z` section to
`CHANGELOG.md` documenting the changes — the workflow extracts that section as the GitHub
Release body (and fails without it); the in-app update dialog and post-update changelog
render the same text.

## Architecture

### Module layout
The Qt app lives under `MyTime/` and uses **flat imports** (bare module names,
resolved via a `sys.path` insert in the entry point), so modules reference each other
directly (`from ui_common import ...`). `from __future__ import annotations` everywhere
keeps type hints lazy, so windows/managers reference each other by duck-typed `self.app`
without import cycles. Key files:
- `ui_common.py` — shared leaf module: fonts/colors/helpers, the `SETTING_*` typography
  constants, `enlarge_control_font`/`set_label_font`, `FluentSettingRow`, `InfoToolTipFilter`,
  `FramelessDragMixin`, `tray_icon`. Imports no app/window/engine code.
- `settings_ui.py` — `SettingsWindow` (a normal-layer window: no StaysOnTop; carries an explicit
  app icon + the process sets its own AppUserModelID so dev runs don't show the pythonw icon in
  the taskbar). `update_ui.py` — update dialogs + `UpdateManager`.
  Never put `add_soft_shadow` (a live `QGraphicsDropShadowEffect`) on a widget that fills its
  top-level window: the shadow is clipped away (invisible) but the effect re-renders + re-blurs
  the whole subtree on every child repaint — this was the settings window's hover jank. The
  shadowed frame/window pairs in settings, history, update, crop and the todo editor popup are
  all full-bleed, so none of them carry the effect.
  Update UI & release-notes spec (aligned with ChatLab): CHANGELOG.md sections open with a
  `> ` one-line summary then `### ✨ 新功能 / 🐛 修复 / ⚡ 性能 / 🗑️ 移除` groups; the release
  workflow copies the section verbatim into the GitHub Release body, and the dialogs render it
  via QTextDocument markdown → exported HTML with typography (line-height, app font, accent
  headings) injected into the inline styles — a QLabel stylesheet line-height never reaches the
  internal rich-text renderer. The changelog is bundled into the build (Build.ps1 --add-data),
  and `updater.local_changelog_section` serves the post-update dialog offline; GitHub is only
  the fallback. `UpdateDialog`/`ChangelogDialog` can be exercised without a release by setting
  `LIQUID_MEMO_DEBUG_RELEASE` (+ optional `LIQUID_MEMO_DEBUG_NOTES`) before startup.
  `calendar_manager.py` — `CalendarManager` + sync tasks.
- `floating_launcher.py` — the painted 72px launcher plus `FloatingModeController`, pure panel-
  placement helpers, and launcher deadline-status calculation.
- `ink_theme.py` — the three selectable 水墨主题 palettes (`InkTheme` / `INK_THEMES`, keyed by
  `settings.inkTheme`: qinghua/warm/blush) plus `InkThemeController`, which tints the chrome
  (settings window, tray menu, add popup, round buttons, launcher bubble) to the chosen theme
  while the ink skin is active and restores the default look when it isn't.
- `ink_swirl.py` — the animated "fluid" background's `QPainter` fallback, used when OpenGL is
  unavailable. Pure `QWidget`/`QPainter` (no GPU, no screen capture); exports
  `SwirlPainterFallback` (the widget), `SwirlThemeTokens`/`SwirlConfig`, `SwirlInteractionController`,
  and `SWIRL_TOKENS_BY_THEME`/`swirl_tokens(theme_key)` (per-ink-theme palettes).
- `ink_background.py` — `make_ink_background(parent, theme_key)`: returns the GPU ink-wash
  (`experimental_fluid.FluidGLWidget`, themed via `_INK_PALETTE_BY_THEME`) when OpenGL is usable,
  else the themed `SwirlPainterFallback`. `app.py` drives whichever it returns through `InkSkin`.
- `app.py` — `MemoWindow` (the translucent window), the memo content widgets/popups,
  `HistoryWindow`, the `AcrylicSkin`/`InkSkin`/`ImageSkin` skins, and `LiquidMemoApp`
  (lifecycle/orchestration); imports the modules above.
- `updater.py` — Qt-free update logic. New first-party modules ship via `--add-data` and are
  imported at runtime, so splitting `app.py` further needs **no `Build.ps1` change** (only new
  *third-party* imports need a `--hidden-import`).

### Two-layer rendering model
`MemoWindow` (in `MyTime/app.py`) is a plain translucent `QWidget`
(`WA_TranslucentBackground`, frameless `Qt.Tool`, topmost). The window's surface is supplied
by the active skin, not by Qt painting:
- **`AcrylicSkin` (default):** `WindowsWindowEffect.setAcrylicEffect` applies a DWM acrylic frost
  to the hwnd; `set_rounded_corners` rounds it. No screen capture, no GPU effects, no per-frame loop.
- **`ImageSkin`:** an `_ImageBackground` child paints a cover-scaled static image below the content.
- **`InkSkin` (kind `"ink"`):** an animated ink-wash surface painted below the
  content, themed to `settings.inkTheme` (qinghua blue / warm sepia / blush rose). The
  background widget comes from `ink_background.make_ink_background(parent, theme_key)` — the GPU
  ink-wash (`experimental_fluid.FluidGLWidget`) when OpenGL is available, else the `QPainter`
  `SwirlPainterFallback`. This is the one **animated** skin (a timer-driven loop, started/stopped on
  show/hide); the "no per-frame loop" note above is specific to the static frost/image skins. The
  invariant that still holds for *every* skin is **no desktop screen
  capture** — animation done in-process is fine; sampling/refracting the desktop
  behind the window is the removed glass path and must not come back.

**All interactive content lives in `self.container`** (a transparent child `QWidget` exposed as
`MemoWindow.content`), created directly in `__init__` and kept sized to the window. All three
skins use full-fill geometry (`geometry_scale = 1.0`), so content fills the window minus a small
corner margin; `_resize_for_content` solves the window height from the content and calls
`setFixedSize`.

**Capture-exclusion policy:** `protect_content_layer()` raises the content layer and calls
`protect_window_from_capture` (`window_layer.py`), which applies the process-wide policy via
`SetWindowDisplayAffinity` (`WDA_EXCLUDEFROMCAPTURE` vs `WDA_NONE`). By default the memo and
launcher opt out of capture so screenshots / recordings of the desktop don't grab
the widget's text; the `行为 → 允许被截屏` toggle (`Settings.allowScreenshot`) flips it. The policy
is a module global set by `window_layer.set_capture_exclusion` — the app sets it from settings
before any window is created and re-applies live via `LiquidMemoApp.apply_capture_policy()` when the
toggle changes (the decoupled launcher reads the policy in its `showEvent` instead of
holding an app reference). It's re-applied on show/move/settings-apply with staggered
`QTimer.singleShot` retries because Windows resets the affinity on various window-state changes.

### Text color is deterministic (no sampling)
There is no live desktop sampling anymore (that was glass-only). `_normal_text_color` picks text
deterministically: `AcrylicSkin` chooses a soft dark/light by the **frost tint's** luminance
(`windowTint`); `ImageSkin` chooses by the **image's mean luminance**, but honors a manual color
when `fontColorMode == "manual"`. `text_needs_halo()` is always `False` (flat surfaces). When
editing text-color logic, do not add a screen-capture/GDI sampler back in.

### Native window behavior (`window_layer.py` + `MemoWindow.nativeEvent`)
The widget handles Win32 messages directly (no Qt-driven move):
- `WM_NCHITTEST` → `HTCAPTION` over the drag handle (native move), `HTCLIENT` over interactive
  controls (checkboxes, buttons), and `HTTRANSPARENT` everywhere else so clicks pass through to
  the desktop. This click-through is the `alwaysVisibleClickThrough` layer mode (the only
  supported `layerMode` — `state_store.py` force-normalizes any other value).
- `showEvent` calls `window_layer.set_hit_testable_layered`: Qt's WA_TranslucentBackground
  windows carry WS_EX_LAYERED in per-pixel (UpdateLayeredWindow) mode, where fully transparent
  pixels (the whole empty surface of the frost/image skins) pass the mouse to the desktop before
  our NCHITTEST handler runs — drag-resize "only worked in ink" because the GL surface paints
  every pixel opaque. `SetLayeredWindowAttributes(LWA_ALPHA, 255)` flips hit-testing to the full
  rect without touching the DWM-composited translucency. (Stripping the LAYERED flag instead
  breaks Qt's translucent composition — the window renders black.)
- Bottom-corner resize hot zones (`MemoWindow._resize_zone`) return `HTBOTTOMLEFT` /
  `HTBOTTOMRIGHT` / `HTBOTTOM` / `HTLEFT` / `HTRIGHT` so Windows runs the native size loop —
  the window uses a min/max size range (not `setFixed*`) to allow this. When `WM_EXITSIZEMOVE`
  ends with a changed size, `_finish_user_resize` pins `window.userSized` + the size;
  `_resize_for_content` then respects the manual size in collapsed mode only (expanded mode and
  the 展开全部/收起 toggle always return to auto-fit) and applies the size in every branch —
  including startup, so a saved manual size survives restarts.
- `WM_ENTERSIZEMOVE/WM_EXITSIZEMOVE` bracket a native move (`_begin_window_move`/`_end_window_move`,
  which just track state, persist position, and re-protect the content layer — the frost / image
  follows the window natively, so there's nothing to spin up).
- Todo rows return `HTCLIENT` for drag-reordering; calendar rows remain
  read-only/click-through outside their checkbox. TodoRow disambiguates click vs drag with a
  `CHECKBOX_ZONE_PAD` forgiveness zone around the checkbox: hovering there shows the pointing
  hand, a press never arms the reorder drag, and release inside the zone toggles the box.
- `window_layer.py` applies tool-window ex-style (no taskbar entry), detaches from any parent,
  and pins topmost.

### Settings → skin dispatch
`apply_settings` resolves `settings.skin` via `_make_skin` and dispatches on the resolved skin's
`kind` to `_apply_acrylic_mode` / `_apply_image_mode` / `_apply_ink_mode`, which swap
the DWM frost, the image layer, or the animated swirl layer. `_make_skin` precedence:
an `"image:<id>"` with a missing file falls back to `AcrylicSkin`; otherwise `"acrylic"` / `"ink"`
map to their skins unconditionally — 灵动水墨 is a normal, always-selectable skin (设置 → 外观).
The swirl/ink-wash colour follows `settings.inkTheme` (`InkSkin.text_override` +
`make_ink_background(parent, theme_key)`; `MemoWindow._ink_theme_key` recolours the background
widget in place when the theme changes). While the ink skin is active, `InkThemeController`
additionally tints the settings/history windows, add popup, tray menu, round buttons and launcher
bubble to the chosen theme (`apply_ink_theme(theme)` swept over top-level widgets; `None` restores
the default look). `windowTint` tints the acrylic frost; the removed `glassOpacity` /
`liquidStrength` settings were glass-only and no longer exist.

### State & persistence (`state_store.py`)
Dataclasses `AppState / Settings / WindowState / TodoItem` serialize to
`%APPDATA%\Roaming\DesktopMemo_Pro\liquid-state.json`. Writes are atomic (temp file +
`replace`); a corrupt file is backed up as `liquid-state.bad-<timestamp>.json` and a fresh
state is returned. Saves are normally debounced through `LiquidMemoApp.save_later()` (350ms);
use `save()` directly only when immediate persistence is required. Completed todos move to
`history` (archive) or stay dimmed in-place depending on `completeBehavior`.
`Settings.windowMode` is one of `normal`, `edgeHide`, or `floatingLauncher`; v4
`edgeAutoHide` state migrates into that enum. The launcher position is stored independently from
the memo position and clamped to the live monitor layout when shown.
State v6 gained `inkTheme` (the 水墨主题 palette, also driving the chrome tint) and
`window.userSized` (set when a drag-resize finishes; collapsed mode then keeps the manual size
instead of auto-fitting, and the 展开全部/收起 toggle clears it). Legacy states that
stored the easter-egg-era `skin: "surprise_swirl"` / `surpriseNoteTheme` keys migrate automatically
to `skin: "ink"` / `inkTheme` in `AppState.from_dict`; the old unlock fields are ignored.

### App lifecycle (`LiquidMemoApp`)
Owns the `QApplication`, the `MemoWindow`, the `SettingsWindow`/`HistoryWindow` dialogs, and
the system tray (`QSystemTrayIcon`). `setQuitOnLastWindowClosed(False)` — closing the window
hides it; exit happens only via the tray menu. `startup.py` toggles a `HKCU\...\Run` registry
entry for launch-at-login.
`FloatingModeController` owns the separate launcher top-level window and decides at startup and
runtime whether to show the memo, edge-dock it, or expose only the launcher. In launcher mode the
memo is an anchored popover and must not overwrite the saved normal-window position.

### Auto-update (`updater.py` + `update_ui.py`)
In-app update over GitHub Releases: `updater.py` is Qt-free network/process logic;
`update_ui.py` owns the dialogs and the `UpdateManager` orchestration. Flow: fetch latest release
(GitHub API, falling back to the rate-limit-free `releases.atom` feed) → if newer, prompt with
release notes → download the `-Setup-*.exe` to `%TEMP%` → **verify SHA256** → spawn a detached
`--apply-update` helper (this same exe) that asks the app to shut down, waits briefly, and—only
after verifying the stuck PID still belongs to that exact executable—force-terminates it if a Qt
worker prevents process exit; it then runs the Inno installer silently and relaunches.
`pendingUpdateVersion` persists across the restart so a failed install surfaces a notice next
launch. The helper records checksum, exit, installer-return-code, and relaunch events in
`%TEMP%\MyTime-update.log`.

**Release-asset contract** (produced by `release.yml` / `Package.ps1`, consumed by `updater.py`):
- `MyTime-Setup-vX.Y.Z.exe` + `.sha256` sidecar (`<hash>  <name>`, sha256sum layout)
- `MyTime-Portable-vX.Y.Z.zip` + `.sha256` sidecar
- The installer is verified against its sidecar before the silent install (mismatch aborts;
  a release with no sidecar — older versions — skips verification rather than fail-closed).
- The portable zip carries a `portable.flag` marker (never in the installer). `is_portable_build()`
  detects it: a portable copy must NOT silent-install over itself, so its update button just opens
  the release page.

The silent startup check is throttled (`Settings.lastUpdateCheckAt`, every 12h), gated by
`Settings.autoCheckUpdates` (a 关于-section toggle), and won't re-prompt for a version the user
dismissed (`Settings.lastDismissedUpdateVersion`); a manual "检查更新" bypasses all of these.

### GPU fluid ink-wash (`MyTime/experimental_fluid/`)
An OpenGL 3.3 Core / `QOpenGLWidget` + PyOpenGL fluid solver (curl → vorticity → divergence →
pressure Jacobi → gradient-subtract → advection → splat, GLSL in `shaders/`). The algorithm was
ported from the WebGL reference (`WebGL-Fluid-Simulation/`, kept locally but **gitignored** — it's
a port source, not needed at runtime; see `THIRD_PARTY_NOTICES.md`). Run the standalone tuner demo:
`python -m MyTime.experimental_fluid.fluid_demo_window`.

This **ships**: `ink_background.make_ink_background(parent, theme_key)` returns `FluidGLWidget`
(themed via `fluid_config.FluidConfig`) as the 灵动水墨 skin's background when OpenGL is usable, and
falls back to the QPainter `ink_swirl.SwirlPainterFallback` otherwise. Both expose the same
lifecycle (`start`/`stop`/`setActive`/`cleanup`/`setGeometry`/`set_theme`); `MemoWindow` drives
whichever it gets through `InkSkin`.
- **PyOpenGL is a real dependency** — present in `requirements.txt` and bundled by `Build.ps1`
  (`--collect-all OpenGL`, the `OpenGL.platform.win32` / `PySide6.QtOpenGL*` hidden-imports). The
  whole `experimental_fluid/` (incl. `shaders/`) ships via the existing `--add-data MyTime`.
- `set_theme(theme_key)` recolours in place (GL uploads the palette uniforms each frame; the swirl
  re-bakes its colour layers), so an ink-theme switch never rebuilds the GL context.
- `fluid_demo_window.py` / `inkwash_tuner.html` are dev-only tools (committed, not used at runtime).
  `_fluid_debug.log` (repo root) and scratch files under `experimental_fluid/` (`*.txt`,
  `*_spike.py`) are gitignored; don't treat them as sources of truth.
