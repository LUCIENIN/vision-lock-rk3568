from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple


def _append_log(line: str) -> None:
    with Path("/tmp/vision_lock_overlay.log").open("a", encoding="utf-8") as fp:
        fp.write(f"{line}\n")
        fp.flush()

try:
    import tkinter as tk
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"Tkinter 不可用，当前系统无法启动本地覆盖锁屏：{exc}")


def _find_active_monitor_rect() -> Optional[Tuple[int, int, int, int]]:
    display = os.environ.get("DISPLAY")
    if not display:
        return None

    xrandr = shutil.which("xrandr")
    if not xrandr:
        return None

    try:
        output = subprocess.check_output(
            [xrandr, "--listactivemonitors"],
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        return None

    re_monitor = re.compile(r"(?P<w>\d+)/\d+x(?P<h>\d+)/\d+(?P<x>[+-]-?\d+)(?P<y>[+-]-?\d+)")
    virtual_w, virtual_h = _probe_virtual_size()
    monitor_pattern = re.compile(r"^\\d+: [^ ]+ (?P<w>\\d+)/(?P<mmw>\\d+)x(?P<h>\\d+)/(?P<mmh>\\d+)(?P<x>[+-]-?\\d+)(?P<y>[+-]-?\\d+)  (?P<name>\\S+)$")

    candidates: list[tuple[int, int, int, int, str]] = []
    preferred = os.environ.get("VISION_LOCK_MONITOR", "").upper()
    raw_lines = output.splitlines()
    for line in raw_lines:
        parts = line.split()
        if len(parts) < 4:
            continue
        name = parts[-1]
        m = re_monitor.search(parts[2]) if len(parts) > 2 else None
        if not m:
            continue
        try:
            w = int(m.group("w"))
            h = int(m.group("h"))
            x = int(m.group("x"))
            y = int(m.group("y"))
            if w > 0 and h > 0:
                if preferred and preferred in name.upper():
                    return _clamp_to_virtual((w, h, x, y), virtual_w, virtual_h)
                if re.search(r"(hdmi|DP|DisplayPort)", name, re.IGNORECASE) is not None:
                    candidates.append((w, h, x, y, name))
        except (TypeError, ValueError):
            continue

    if not candidates:
        for line in raw_lines:
            parts = line.split()
            if len(parts) < 4:
                continue
            name = parts[-1]
            m = re_monitor.search(parts[2]) if len(parts) > 2 else None
            if not m:
                continue
            try:
                w = int(m.group("w"))
                h = int(m.group("h"))
                x = int(m.group("x"))
                y = int(m.group("y"))
                if w > 0 and h > 0:
                    if preferred and preferred in line.upper():
                        return _clamp_to_virtual((w, h, x, y), virtual_w, virtual_h)
                    return _clamp_to_virtual((w, h, x, y), virtual_w, virtual_h)
            except (TypeError, ValueError):
                continue
        return None

    candidates.sort(key=lambda item: item[0] * item[1], reverse=True)
    return _clamp_to_virtual(tuple(candidates[0][:4]), virtual_w, virtual_h)


def _clamp_to_virtual(
    geometry: Tuple[int, int, int, int],
    virtual_w: int | None,
    virtual_h: int | None,
) -> Tuple[int, int, int, int]:
    width, height, x, y = geometry
    if virtual_w is not None:
        x = max(0, min(x, max(0, virtual_w - width)))
    else:
        x = 0
    if virtual_h is not None:
        y = max(0, min(y, max(0, virtual_h - height)))
    else:
        y = 0
    return (width, height, x, y)


def _probe_virtual_size() -> Tuple[Optional[int], Optional[int]]:
    xrandr = shutil.which("xrandr")
    if not xrandr:
        return _probe_virtual_size_fbset()

    try:
        output = subprocess.check_output([xrandr, "--query"], text=True, timeout=2)
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        return _probe_virtual_size_fbset()

    match = re.search(r"current\\s+(\\d+)\\s+x\\s+(\\d+)", output)
    if not match:
        return _probe_virtual_size_fbset()
    return int(match.group(1)), int(match.group(2))


def _probe_virtual_size_fbset() -> Tuple[Optional[int], Optional[int]]:
    fbset = shutil.which("fbset")
    if not fbset:
        return None, None
    try:
        output = subprocess.check_output([fbset], text=True, timeout=2)
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        return None, None

    match = re.search(r"geometry\\s+(\\d+)\\s+(\\d+)", output)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _load_and_fit_image(image_path: Path, target_w: int, target_h: int):
    from PIL import Image

    image = Image.open(image_path)
    image = image.convert("RGB")
    if target_w <= 1 or target_h <= 1:
        return image, None

    iw, ih = image.size
    if iw <= 0 or ih <= 0:
        return image, None

    resize_mode = os.environ.get("VISION_LOCK_OVERLAY_STRETCH", "0").strip().lower()
    if resize_mode in {"1", "true", "yes", "on", "y"}:
        new_w, new_h = target_w, target_h
        resized = image.resize(
            (new_w, new_h),
            resample=Image.Resampling.BICUBIC if hasattr(Image, "Resampling") else Image.ANTIALIAS,
        )
        return resized, None

    scale = min(target_w / float(iw), target_h / float(ih))
    new_w = max(1, min(target_w, int(round(iw * scale))))
    new_h = max(1, min(target_h, int(round(ih * scale))))
    resized = image.resize(
        (new_w, new_h),
        resample=Image.Resampling.BICUBIC if hasattr(Image, "Resampling") else Image.ANTIALIAS,
    )
    if new_w == target_w and new_h == target_h:
        return resized, None

    canvas = Image.new("RGB", (target_w, target_h), "#000000")
    x = (target_w - new_w) // 2
    y = (target_h - new_h) // 2
    canvas.paste(resized, (x, y))
    return canvas, (x, y, new_w, new_h)


def _probe_display_size() -> tuple[int, int]:
    """Fallback actual display size used by overlay when monitor auto-detect fails."""
    try:
        output = subprocess.check_output(["xrandr", "--query"], text=True, timeout=2)
        match = re.search(r"current\s+(\d+)\s+x\s+(\d+)", output)
        if match:
            width = int(match.group(1))
            height = int(match.group(2))
            if width > 0 and height > 0:
                return width, height
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired, ValueError):
        pass

    try:
        output = subprocess.check_output(["xdpyinfo"], text=True, timeout=2)
        match = re.search(r"dimensions:\s+(\d+)x(\d+)", output)
        if match:
            width = int(match.group(1))
            height = int(match.group(2))
            if width > 0 and height > 0:
                return width, height
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired, ValueError):
        pass

    return (1366, 768)


def _safe_attributes(root: tk.Misc, name: str, *values) -> None:
    try:
        root.attributes(name, *values)
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--seconds", type=int, default=12)
    args = parser.parse_args()

    if "DISPLAY" not in os.environ:
        os.environ["DISPLAY"] = ":0"

    stretch_env = os.environ.get("VISION_LOCK_OVERLAY_STRETCH", "0").strip().lower()
    fullscreen_env = os.environ.get("VISION_LOCK_OVERLAY_FULLSCREEN", "1").strip().lower() in {"1", "true", "yes", "on", "y"}
    _append_log(f"overlay_launch image={args.image} seconds={args.seconds} stretch={stretch_env} fullscreen={fullscreen_env}")

    try:
        root = tk.Tk()
        root.title("vision-lock-overlay")
        root.overrideredirect(True)
        root.configure(bg="#000000")

        # ── 立即隐藏，全部就绪后再显示 ──
        root.withdraw()

        # ── 分辨率检测 + geometry ──
        geometry = _find_active_monitor_rect()
        if geometry is not None:
            width, height, x, y = geometry
            _append_log(f"overlay_geometry={width}x{height}+{x}+{y}")
            root.geometry(f"{width}x{height}+{x}+{y}")
        else:
            width, height = _probe_display_size()
            _append_log(f"overlay_probe={width}x{height}  (no xrandr monitor)")
            root.geometry(f"{width}x{height}+0+0")

        # ── 加载图片（窗口隐藏状态下完成）──
        img_path = Path(args.image)
        tk_image = None
        if img_path.exists():
            from PIL import Image, ImageTk
            image, letterbox = _load_and_fit_image(img_path, width, height)
            if letterbox:
                _append_log("overlay_letterbox=x{0},y{1},w{2},h{3}".format(*letterbox))
            else:
                _append_log("overlay_letterbox=no")
            tk_image = ImageTk.PhotoImage(image)

        # ── 创建完整 UI ──
        frame = tk.Frame(root, width=width, height=height)
        frame.pack(fill="both", expand=True)

        if tk_image is not None:
            label = tk.Label(frame, image=tk_image, borderwidth=0, highlightthickness=0)
            label.image = tk_image
            label.pack(fill="both", expand=True, padx=0, pady=0)
        else:
            _append_log(f"overlay_image_missing path={args.image}")
            label = tk.Label(frame, text="视觉锁屏触发", font=("Helvetica", 28), fg="#fff", bg="#111")
            label.pack(expand=True)

        # ── 一次性设置属性 ──
        if fullscreen_env:
            _safe_attributes(root, "-fullscreen", True)
        _safe_attributes(root, "-topmost", True)
        root.resizable(False, False)

        # ── 全部就绪，一次显示 ──
        root.deiconify()
        root.lift()
        root.focus_force()

        # ── 全局抓取，阻止抢焦点 ──
        try:
            root.grab_set_global()
        except Exception:
            root.grab_set()

        # ── 绑定关闭 ──
        root.bind("<Escape>", lambda *_: root.destroy())
        root.after(args.seconds * 1000, root.destroy)

        start = time.monotonic()
        root.mainloop()
        runtime = int((time.monotonic() - start) * 1000)
        _append_log(f"overlay_run_ms={runtime}")
    except Exception as exc:
        _append_log(f"overlay_error={type(exc).__name__}:{exc}")
        raise


if __name__ == "__main__":
    main()
