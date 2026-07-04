"""Theme pack system — qylock-inspired self-contained theme directories."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


@dataclass
class ThemeColors:
    accent: str = "#0ea5e9"
    bg_top: str = "#0b1220"
    bg_bottom: str = "#0f172a"
    title: str = "#e2e8f0"
    body: str = "#cbd5e1"
    extras: Dict[str, str] = field(default_factory=dict)


@dataclass
class ThemeFonts:
    title_size_ratio: float = 0.048
    body_size_ratio: float = 0.023
    font_scale: float = 1.0
    preferred: List[str] = field(default_factory=list)


@dataclass
class ThemeDecor:
    lock_badge: bool = False
    grid_lines: bool = False
    scanline: bool = False
    bar_graphics: bool = False
    eastern_russian: bool = False
    neon_glow: bool = False
    banner: bool = False
    frost_panel: bool = False
    modules: List[str] = field(default_factory=list)


@dataclass
class ThemeLayout:
    header_ratio: float = 0.24
    card_margin_ratio: float = 0.21
    pad_x_ratio: float = 0.03
    pad_y_ratio: float = 0.022
    border_width: int = 1


@dataclass
class Theme:
    """A single theme loaded from themes/<name>/theme.yaml"""

    name: str
    display_name: str = ""
    description: str = ""
    colors: ThemeColors = field(default_factory=ThemeColors)
    fonts: ThemeFonts = field(default_factory=ThemeFonts)
    decor: ThemeDecor = field(default_factory=ThemeDecor)
    layout: ThemeLayout = field(default_factory=ThemeLayout)
    dir: Path = Path()

    @property
    def assets_dir(self) -> Path:
        return self.dir / "assets"

    def asset_path(self, filename: str) -> Optional[Path]:
        p = self.assets_dir / filename
        return p if p.exists() else None


def load_theme(name: str, base_dir: Path) -> Theme:
    """Load a theme from themes/<name>/theme.yaml"""
    theme_dir = base_dir / name
    yaml_path = theme_dir / "theme.yaml"

    if not yaml_path.exists():
        return _fallback_theme(name, theme_dir)

    with yaml_path.open("r", encoding="utf-8") as f:
        data: Dict[str, Any] = yaml.safe_load(f) or {}

    raw_colors = data.get("colors", {})
    raw_fonts = data.get("fonts", {})
    raw_decor = data.get("decor", {}) or data.get("decorations", {})
    raw_layout = data.get("layout", {})
    extra_colors = {k: v for k, v in raw_colors.items()
                    if k not in ("accent", "bg_top", "bg_bottom", "title", "body")}

    modules = raw_decor.get("modules", [])
    if not modules:
        modules = [k for k, v in raw_decor.items() if isinstance(v, bool) and v]

    return Theme(
        name=name,
        display_name=data.get("display_name", name),
        description=data.get("description", ""),
        colors=ThemeColors(
            accent=raw_colors.get("accent", "#0ea5e9"),
            bg_top=raw_colors.get("bg_top", "#0b1220"),
            bg_bottom=raw_colors.get("bg_bottom", "#0f172a"),
            title=raw_colors.get("title", "#e2e8f0"),
            body=raw_colors.get("body", "#cbd5e1"),
            extras=extra_colors,
        ),
        fonts=ThemeFonts(
            title_size_ratio=raw_fonts.get("title_size_ratio", 0.048),
            body_size_ratio=raw_fonts.get("body_size_ratio", 0.023),
            font_scale=raw_fonts.get("font_scale", 1.0),
            preferred=raw_fonts.get("preferred", []),
        ),
        decor=ThemeDecor(
            modules=modules,
            lock_badge="lock_badge" in modules,
            grid_lines="grid_lines" in modules,
            scanline="scanline" in modules,
            bar_graphics="bar_graphics" in modules,
            eastern_russian="eastern_russian" in modules,
            neon_glow="neon_glow" in modules,
            banner="banner" in modules,
            frost_panel="frost_panel" in modules,
        ),
        layout=ThemeLayout(
            header_ratio=raw_layout.get("header_ratio", 0.24),
            card_margin_ratio=raw_layout.get("card_margin_ratio", 0.21),
            pad_x_ratio=raw_layout.get("pad_x_ratio", 0.03),
            pad_y_ratio=raw_layout.get("pad_y_ratio", 0.022),
            border_width=raw_layout.get("border_width", 1),
        ),
        dir=theme_dir,
    )


def _fallback_theme(name: str, theme_dir: Path) -> Theme:
    """Minimal fallback so missing theme.yaml never crashes."""
    return Theme(
        name=name,
        display_name=name,
        dir=theme_dir,
    )


def available_themes(base_dir: Path) -> List[str]:
    """List theme names available under base_dir."""
    if not base_dir.is_dir():
        return []
    return sorted(
        d.name for d in base_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )
