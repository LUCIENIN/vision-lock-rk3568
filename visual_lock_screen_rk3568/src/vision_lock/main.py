from __future__ import annotations

import argparse
import os
import time
import traceback
from pathlib import Path

from .config import load_config
from .engine import VisionLockEngine
from .content import build_content
from . import lockscreen


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RK3568 视觉识别锁屏")
    parser.add_argument("--config", default="config/example.yaml")
    parser.add_argument("--once", action="store_true", help="只执行一次锁屏渲染+展示，不启动持续识别")
    parser.add_argument("--seconds", type=int, default=12, help="一次模式下显示秒数（覆盖配置中的 auto_unlock_seconds）")
    parser.add_argument(
        "--style",
        choices=["classic", "github_wallpaper", "fancy", "cyberpunk", "cyber", "plain", "safe", "text", "high_contrast"],
        help="临时覆盖样式（一次模式下生效）",
    )
    return parser.parse_args()


def _append_log(line: str) -> None:
    with Path("/tmp/vision_lock_runtime.log").open("a", encoding="utf-8", errors="ignore") as fp:
        fp.write(f"{line}\n")
        fp.flush()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    if args.style:
        cfg.design.style = args.style
    else:
        force_style = os.environ.get("VISION_LOCK_FORCE_STYLE", "").strip().lower()
        if force_style:
            cfg.design.style = force_style

    if args.once:
        lock_cfg = cfg.lock
        lock_cfg.auto_unlock_seconds = args.seconds
        content = build_content(cfg.design.headline, cfg.design.subheadline, cfg.content)
        lockscreen.execute_lock(cfg.design, lock_cfg, content, fallback_mode="overlay")
        return

    _append_log(
        "config_loaded "
        f"camera={cfg.camera.source}@{cfg.camera.width}x{cfg.camera.height}@{cfg.camera.fps} "
        f"detection=person_confidence={cfg.detection.person_confidence} trigger_frames={cfg.detection.trigger_frames} cooldown={cfg.detection.cooldown_seconds} "
        f"lock.mode={cfg.lock.mode} style={cfg.design.style} api_tip={bool(cfg.content.tip_api)} "
        f"cache={cfg.content.cache_file}"
    )

    while True:
        try:
            engine = VisionLockEngine(cfg)
            try:
                engine.run()
            finally:
                engine.close()
        except KeyboardInterrupt:
            break
        except Exception as exc:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip().replace("\n", "\\n")
            _append_log(f"runtime_err={type(exc).__name__} {exc}")
            _append_log(f"runtime_tb={tb}")
            time.sleep(3)


if __name__ == "__main__":
    main()
