from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


@dataclass
class CameraConfig:
    source: str | int = "/dev/video0"
    width: int = 1280
    height: int = 720
    fps: int = 10


@dataclass
class DetectionConfig:
    method: str = "auto"
    person_confidence: float = 0.01
    trigger_frames: int = 1
    cooldown_seconds: int = 8


@dataclass
class LockConfig:
    mode: str = "auto"
    image_path: str = "/tmp/visual_lock_screen.png"
    auto_unlock_seconds: int = 12
    i3lock_command: list[str] | None = None


@dataclass
class DesignConfig:
    headline: str = "视觉已检测到人形"
    subheadline: str = "锁屏模式已触发"
    style: str = "classic"
    theme_dir: str = ""
    accent_color: str = "#0ea5e9"
    bg_color_top: str = "#0b1220"
    bg_color_bottom: str = "#0f172a"
    title_color: str = "#e2e8f0"
    body_color: str = "#cbd5e1"


@dataclass
class ContentConfig:
    tip_api: str = ""
    cache_file: str = "/tmp/visual_lock_screen_cache.json"


@dataclass
class AppConfig:
    camera: CameraConfig
    detection: DetectionConfig
    lock: LockConfig
    design: DesignConfig
    content: ContentConfig


def _load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fp:
        data = yaml.safe_load(fp) or {}
    return data


def _resolve_theme_dir(raw: Dict[str, Any], config_path: Path) -> str:
    """Resolve theme_dir relative to config file location."""
    td = raw.get("design", {}).get("theme_dir", "")
    if not td:
        # Default: themes/ next to config file
        return str(config_path.resolve().parent.parent / "themes")
    td_path = Path(td)
    if not td_path.is_absolute():
        td_path = config_path.resolve().parent / td_path
    return str(td_path)


def load_config(path: str) -> AppConfig:
    cfg_path = Path(path)
    raw = _load_yaml(cfg_path)
    design_raw = raw.get("design", {})
    # Resolve theme_dir before constructing DesignConfig
    resolved_td = _resolve_theme_dir(raw, cfg_path)
    design_raw["theme_dir"] = resolved_td
    return AppConfig(
        camera=CameraConfig(**raw.get("camera", {})),
        detection=DetectionConfig(**raw.get("detection", {})),
        lock=LockConfig(**raw.get("lock", {})),
        design=DesignConfig(**design_raw),
        content=ContentConfig(**raw.get("content", {})),
    )
