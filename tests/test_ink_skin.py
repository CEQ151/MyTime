"""灵动水墨 (ink) skin: state persistence, migration from the old easter-egg keys, and the
theme-token plumbing that drives the background and chrome tint."""
from dataclasses import asdict
from types import SimpleNamespace

from PySide6.QtCore import QRect

from state_store import AppState


def test_ink_theme_roundtrips():
    payload = asdict(AppState())
    payload["settings"].update(skin="ink", inkTheme="blush")

    restored = AppState.from_dict(payload)

    assert restored.settings.skin == "ink"
    assert restored.settings.inkTheme == "blush"


def test_legacy_surprise_state_migrates_to_ink():
    payload = asdict(AppState())
    # Pre-rename states stored the swirl under "surprise_swirl" and the palette under
    # "surpriseNoteTheme", plus unlock fields that no longer exist.
    payload["settings"].update(
        skin="surprise_swirl",
        surpriseNoteTheme="warm",
        surpriseEnabled=True,
        surpriseKeyBlob="sealed-key",
        preSurpriseSkin="acrylic",
    )
    # A legacy state file predates the inkTheme key entirely; drop the new default so the
    # payload matches what old builds actually wrote.
    payload["settings"].pop("inkTheme")

    restored = AppState.from_dict(payload)

    assert restored.settings.skin == "ink"
    assert restored.settings.inkTheme == "warm"


def test_unknown_skin_falls_back_to_acrylic():
    payload = asdict(AppState())
    payload["settings"].update(skin="glass")  # removed skin

    assert AppState.from_dict(payload).settings.skin == "acrylic"


def test_unknown_ink_theme_falls_back_to_qinghua():
    payload = asdict(AppState())
    payload["settings"].update(inkTheme="neon")

    assert AppState.from_dict(payload).settings.inkTheme == "qinghua"


def test_make_skin_resolves_ink_without_any_unlock_state(qapp):
    from app import AcrylicSkin, InkSkin, MemoWindow

    memo = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(inkTheme="warm", customSkins=[])))
    )

    skin = MemoWindow._make_skin(memo, "ink")

    assert isinstance(skin, InkSkin)
    assert skin.text_override == "#3A2A22"  # warm theme's overlay-safe text colour
    assert isinstance(MemoWindow._make_skin(memo, "acrylic"), AcrylicSkin)


def test_ink_mode_reserves_a_readable_memo_width(qapp):
    # MIN_WIDTH is derived from row_fixed_chrome (≥ INK_MIN_WIDTH since the ❗-clipping fix):
    # the empty-view floor is the derived MIN_WIDTH for every skin now.
    from app import MIN_WIDTH, MemoWindow

    memo = SimpleNamespace(app=None, skin=SimpleNamespace(kind="ink"))

    assert MemoWindow._adaptive_width(memo, [], [], QRect(0, 0, 1920, 1080)) == MIN_WIDTH
    assert MIN_WIDTH > 400  # widest row shape (DDL + ❗ + ✎ + subtask toggle/badge) must fit

    memo.skin = SimpleNamespace(kind="acrylic")
    assert MemoWindow._adaptive_width(memo, [], [], QRect(0, 0, 1920, 1080)) == MIN_WIDTH


def test_swirl_fallback_set_theme_recolours_in_place(qapp):
    from ink_swirl import SwirlPainterFallback, swirl_tokens

    bg = SwirlPainterFallback(tokens=swirl_tokens("qinghua"))
    assert bg.tokens is swirl_tokens("qinghua")

    bg.set_theme("warm")  # in-place recolour, no widget rebuild
    assert bg.tokens is swirl_tokens("warm")
    assert bg.tokens.text_overlay_safe == "#3A2A22"

    bg.cleanup()


def test_ink_background_theme_palette_follows_theme_key(qapp):
    from ink_background import _ink_palette

    assert _ink_palette("qinghua") == ("#eae5c8", "#3d7ab3", "#0154a7")
    assert _ink_palette("warm") == ("#efe7d4", "#bf7d5e", "#8c3b2f")
    assert _ink_palette("blush") == ("#f6ebe9", "#c96a86", "#8e2f54")
    assert _ink_palette("unknown") == _ink_palette("qinghua")
