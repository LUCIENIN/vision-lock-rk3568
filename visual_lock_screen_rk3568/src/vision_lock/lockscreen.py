from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Sequence, Tuple, List

from PIL import Image, ImageDraw, ImageFont

from .config import DesignConfig, LockConfig
from .content import LockContent


def _compat_rounded_rectangle(
    draw: ImageDraw.ImageDraw,
    box,
    radius: int = 0,
    fill=None,
    outline=None,
    width: int = 1,
) -> None:
    kwargs = {}
    if fill is not None:
        kwargs["fill"] = fill
    if outline is not None:
        kwargs["outline"] = outline
    if width is not None:
        kwargs["width"] = width
    draw.rectangle(box, **kwargs)


if not hasattr(ImageDraw.ImageDraw, "rounded_rectangle"):
    ImageDraw.ImageDraw.rounded_rectangle = _compat_rounded_rectangle  # type: ignore[attr-defined]


if not hasattr(ImageDraw.ImageDraw, "textlength"):
    def _compat_textlength(self: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont | None = None) -> int:
        return int(self.textsize(text, font=font)[0])

    ImageDraw.ImageDraw.textlength = _compat_textlength  # type: ignore[attr-defined]


def _compat_font_line_height(font: ImageFont.FreeTypeFont) -> int:
    sample = _text_safe("中") if _FONT_SUPPORTS_CHINESE else "A"
    if hasattr(font, "getbbox"):
        return int(font.getbbox(sample)[3] - font.getbbox(sample)[1])
    if hasattr(font, "getsize"):
        return int(font.getsize(sample)[1])
    return 16


def _append_log(line: str) -> None:
    with Path("/tmp/vision_lock_runtime.log").open("a", encoding="utf-8", errors="ignore") as fp:
        fp.write(f"{line}\n")
        fp.flush()


def _save_image_atomically(img: Image.Image, output_path: Path) -> None:
    temp_path = Path(f"{output_path}.png.tmp")
    img.convert("RGB").save(temp_path, format="PNG")
    temp_path.replace(output_path)


_FONT_SUPPORTS_CHINESE = False
_FONT_LOG_PATH = Path("/tmp/vision_lock_font_choice.log")


def _append_font_log(line: str) -> None:
    try:
        _FONT_LOG_PATH.write_text(f"{line}\n", encoding="utf-8", errors="ignore")
    except Exception:
        pass


def _text_safe(value: str) -> str:
    return value


def _pick_chinese_font(size_title: int, size_body: int) -> Tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont]:
    global _FONT_SUPPORTS_CHINESE

    _append_font_log("font_probe_start")
    font_candidates = [
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in font_candidates:
        try:
            for index in (None, 0):
                title_font = (
                    ImageFont.truetype(path, size_title)
                    if index is None
                    else ImageFont.truetype(path, size_title, index=index)
                )
                body_font = (
                    ImageFont.truetype(path, size_body)
                    if index is None
                    else ImageFont.truetype(path, size_body, index=index)
                )
                _FONT_SUPPORTS_CHINESE = True
                _append_font_log(f"font={path},index={index if index is not None else 'default'},size={size_title}/{size_body}")
                return title_font, body_font
        except (OSError, TypeError, IndexError) as exc:
            _append_font_log(f"font_candidate_error path={path} err={type(exc).__name__}:{exc}")
            continue

    try:
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size_title)
        body_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size_body)
        _FONT_SUPPORTS_CHINESE = False
        _append_font_log(f"font=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf,size={size_title}/{size_body}")
        return (
            title_font,
            body_font,
        )
    except (OSError, TypeError) as exc:
        _append_font_log(f"font_dejavu_error err={type(exc).__name__}:{exc}")
        fallback = ImageFont.load_default()
        _FONT_SUPPORTS_CHINESE = False
        _append_font_log("font=load_default")
        return fallback, fallback


@dataclass
class RenderResult:
    image_path: Path


def _hex_to_rgb(value: str) -> Tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def _mix(a: int, b: int, ratio: float) -> int:
    return int(a * (1 - ratio) + b * ratio)


def _resolve_canvas_size(default_size: Tuple[int, int] = (1920, 1080)) -> Tuple[int, int]:
    fallback_width, fallback_height = default_size
    forced = os.environ.get("VISION_LOCK_CANVAS_SIZE", "").strip()
    if forced:
        forced_match = re.search(r"^(\d+)\s*[xX*]\s*(\d+)$", forced)
        if forced_match:
            forced_w = int(forced_match.group(1))
            forced_h = int(forced_match.group(2))
            if forced_w > 0 and forced_h > 0:
                return forced_w, forced_h

    candidates: list[tuple[str, int, int]] = []
    env = os.environ.copy()
    min_width = min(fallback_width, 1024)
    min_height = min(fallback_height, 576)

    try:
        output = subprocess.check_output(["xrandr", "--query"], text=True, env=env, timeout=2)
        output_re = re.compile(
            r"^(?P<name>\S+)\s+connected(?:\s+primary)?\s+(?P<w>\d+)x(?P<h>\d+)(?:[+.]\d+[+.]\d+)?",
            re.MULTILINE,
        )
        m = re.search(r"current\s+(\d+)\s+x\s+(\d+)", output)
        if m:
            w = int(m.group(1))
            h = int(m.group(2))
            if w > 0 and h > 0:
                candidates.append(("xrandr_current", w, h))

        outputs = []
        for match in output_re.finditer(output):
            w = int(match.group("w"))
            h = int(match.group("h"))
            outputs.append((match.group("name"), w, h))

        if outputs:
            external = [item for item in outputs if not re.search(r"(lvds|dsi)", item[0], re.IGNORECASE)]
            candidates.extend(
                ("xrandr_connected_external" if item in external else "xrandr_connected", item[1], item[2])
                for item in (external if external else outputs)
            )

        if m:
            w = int(m.group(1))
            h = int(m.group(2))
            if w > 0 and h > 0:
                candidates.append(("xrandr_current_second_pass", w, h))

    except (subprocess.TimeoutExpired, OSError, ValueError, subprocess.CalledProcessError):
        pass
    if candidates:
        filtered = [(name, w, h) for name, w, h in candidates if w >= min_width and h >= min_height]
        if filtered:
            candidates = filtered

    if candidates:
        sorted_candidates = sorted(candidates, key=lambda item: item[1] * item[2], reverse=True)
        if sorted_candidates:
            preferred = sorted_candidates[0]
            if preferred[1] == preferred[2] and fallback_width != fallback_height:
                allow_square = os.environ.get("VISION_LOCK_ALLOW_SQUARE_CANVAS", "0").strip().lower() in {"1", "true", "yes", "on"}
                nonsquare_candidates = [item for item in sorted_candidates if item[1] != item[2]]
                if not allow_square and nonsquare_candidates:
                    preferred = nonsquare_candidates[0]
                else:
                    return fallback_width, fallback_height
            if preferred[1] > 0 and preferred[2] > 0:
                return preferred[1], preferred[2]

    try:
        fb_mode = Path("/sys/class/graphics/fb0/modes").read_text(encoding="utf-8", errors="ignore")
        for line in fb_mode.splitlines():
            m = re.search(r"(\d+)x(\d+)", line.strip())
            if m:
                w = int(m.group(1))
                h = int(m.group(2))
                if w > 0 and h > 0 and w >= min_width and h >= min_height:
                    return w, h
    except OSError:
        pass

    try:
        output = subprocess.check_output(["xdpyinfo"], text=True, env=os.environ.copy(), timeout=2)
        m = re.search(r"dimensions:\s+(\d+)x(\d+)", output)
        if m:
            w = int(m.group(1))
            h = int(m.group(2))
            if w > 0 and h > 0 and w >= min_width and h >= min_height:
                return w, h
    except (subprocess.TimeoutExpired, OSError, ValueError, subprocess.CalledProcessError):
        pass

    try:
        output = subprocess.check_output(["fbset"], text=True, env=os.environ.copy(), timeout=2)
        m = re.search(r"geometry\s+(\d+)\s+(\d+)", output)
        if m:
            w = int(m.group(1))
            h = int(m.group(2))
            if w > 0 and h > 0 and w >= min_width and h >= min_height:
                return w, h
    except (subprocess.TimeoutExpired, OSError, ValueError, subprocess.CalledProcessError):
        pass
    return default_size


def _draw_gradient(img: Image.Image, top: Tuple[int, int, int], bottom: Tuple[int, int, int]) -> None:
    width, height = img.size
    draw = ImageDraw.Draw(img)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        r = int(top[0] * (1 - ratio) + bottom[0] * ratio)
        g = int(top[1] * (1 - ratio) + bottom[1] * ratio)
        b = int(top[2] * (1 - ratio) + bottom[2] * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))


def _weekday_cn() -> str:
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return _text_safe(weekdays[datetime.now().weekday()])


def _blend_color(base: Tuple[int, int, int], tint: Tuple[int, int, int], ratio: float) -> Tuple[int, int, int]:
    return tuple(_mix(a, b, ratio) for a, b in zip(base, tint))


def _draw_frost_panel(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    fill: Tuple[int, int, int],
    border: Tuple[int, int, int],
    radius: int = 20,
    alpha: int = 160,
) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    overlay = ImageDraw.Draw(layer)
    overlay.rounded_rectangle(
        (x, y, x + w, y + h),
        radius=radius,
        fill=(fill[0], fill[1], fill[2], alpha),
        outline=(border[0], border[1], border[2], 155),
        width=2,
    )
    img.paste(Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB"))


def _draw_lock_badge(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    radius: int,
    accent: Tuple[int, int, int],
    on: Tuple[int, int, int],
) -> None:
    r = radius
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*accent, 240))
    draw.rounded_rectangle((cx - radius // 2, cy - 2, cx + radius // 2, cy + 18), radius=4, fill=(8, 11, 19, 240))
    draw.rounded_rectangle((cx - radius // 3, cy + 4, cx + radius // 3, cy + 16), radius=2, fill=on)
    draw.arc((cx - radius // 2, cy - radius // 4, cx + radius // 2, cy + radius // 4), start=200, end=340, fill=(*on, 255), width=6)


def _draw_grid_lines(draw: ImageDraw.ImageDraw, width: int, height: int, color: Tuple[int, int, int]) -> None:
    spacing = max(28, int(min(width, height) * 0.035))
    for x in range(0, width + 1, spacing):
        x2 = x
        alpha = max(18, 78 - abs(x - width // 2) * 60 // max(width // 2, 1))
        alpha_color = (color[0], color[1], color[2], alpha)
        segment_h = 10
        for y in range(0, height, segment_h * 4):
            if (x // spacing) % 2 == 0:
                draw.line((x2, y, x2, y + segment_h), fill=alpha_color, width=1)
    for y in range(0, height + 1, spacing):
        y2 = y
        alpha = max(16, 68 - abs(y - height // 2) * 50 // max(height // 2, 1))
        alpha_color = (color[0], color[1], color[2], alpha)
        segment_w = 12
        for x in range(0, width, segment_w * 4):
            if (y // spacing) % 2 == 1:
                draw.line((x, y2, x + segment_w, y2), fill=alpha_color, width=1)


def _draw_scanline(draw: ImageDraw.ImageDraw, width: int, height: int, color: Tuple[int, int, int]) -> None:
    t = int(datetime.now().timestamp())
    phase = t % 12
    scan_alpha = (color[0], color[1], color[2], 24 + phase)
    focus_alpha = (255, 255, 255, 60)
    for i in range(6):
        y = int((i + 1) * (height / 7))
        width_ratio = 0.75 + (i % 2) * 0.2
        draw.line((0, y, int(width * width_ratio), y), fill=scan_alpha, width=1)
        if i in {1, 3, 5}:
            draw.line((int(width * 0.12), y + 1, int(width * 0.88), y + 1), fill=scan_alpha, width=1)
    y_focus = (t * 3) % height
    draw.line((0, y_focus, width, y_focus), fill=focus_alpha, width=2)
    draw.rectangle((0, y_focus - 2, width, y_focus + 2), fill=(color[0], color[1], color[2], 18))


def _draw_bar_graphics(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    accent: Tuple[int, int, int],
    body: Tuple[int, int, int],
) -> None:
    step = max(12, w // 14)
    top = y + 18
    graph_base = y + h - 10
    draw.line((x + 8, top, x + 8, graph_base), fill=(*accent, 120), width=1)
    draw.line((x + 8, graph_base, x + w - 8, graph_base), fill=(*accent, 120), width=1)
    for i in range(14):
        bar_x = x + 16 + (i * step)
        bar_h = 6 + ((i * 3) % 18)
        if bar_x + 7 > x + w:
            break
        draw.rounded_rectangle(
            (bar_x, graph_base - bar_h, bar_x + 10, graph_base),
            radius=3,
            fill=body,
        )
        if i % 3 == 0:
            draw.rounded_rectangle(
                (bar_x - 1, graph_base - bar_h - 3, bar_x + 11, graph_base - bar_h + 2),
                radius=3,
                outline=(*accent, 190),
                width=1,
            )


def _draw_eastern_russian_motif(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    accent: Tuple[int, int, int],
    electric: Tuple[int, int, int],
    magenta: Tuple[int, int, int],
    title_font: ImageFont.FreeTypeFont,
    body_font: ImageFont.FreeTypeFont,
) -> None:
    side_x = width // 7
    lamp_x = side_x
    lamp_y = max(72, int(height * 0.08))
    lamp_h = max(118, int(height * 0.16))
    lamp_w = max(32, int(width * 0.023))
    # 清式灯笼轮廓
    draw.rounded_rectangle(
        (lamp_x, lamp_y, lamp_x + lamp_w, lamp_y + lamp_h),
        radius=8,
        fill=(154, 12, 24),
        outline=(255, 222, 128),
        width=2,
    )
    draw.rounded_rectangle((lamp_x - 6, lamp_y - 10, lamp_x + lamp_w + 6, lamp_y + 6), radius=6, fill=(166, 22, 34), outline=(255, 222, 128), width=2)
    draw.rounded_rectangle((lamp_x - 8, lamp_y + lamp_h, lamp_x + lamp_w + 8, lamp_y + lamp_h + 12), radius=4, fill=(166, 22, 34), outline=(255, 222, 128), width=2)
    for i in range(6):
        y = lamp_y + 14 + i * 14
        draw.line((lamp_x + 1, y, lamp_x + lamp_w - 1, y), fill=(244, 214, 128), width=1)
    draw.polygon(
        [(lamp_x - 4, lamp_y + lamp_h + 2), (lamp_x + lamp_w + 4, lamp_y + lamp_h + 2), (lamp_x + lamp_w // 2, lamp_y + lamp_h + 20)],
        fill=(166, 22, 34),
        outline=(255, 222, 128),
    )
    draw.text((lamp_x - 8, lamp_y - 34), _text_safe("福"), font=body_font, fill=(244, 214, 128))
    draw.text((lamp_x + lamp_w // 2 - 4, lamp_y + 2), _text_safe("囍"), font=body_font, fill=(255, 238, 170))
    draw.text((lamp_x + lamp_w + 6, lamp_y + lamp_h - 8), _text_safe("灯"), font=body_font, fill=(255, 238, 170))

    # 东西方纹理横向组合（祥云 + 俄式切角装饰）
    cloud_y = lamp_y + 42
    for i in range(4):
        base_x = lamp_x + 130 + i * 110
        base_y = cloud_y + (i % 2) * 8
        draw.arc((base_x, base_y, base_x + 92, base_y + 38), start=180, end=360, fill=electric, width=3)
        draw.arc((base_x + 26, base_y - 8, base_x + 108, base_y + 24), start=180, end=360, fill=magenta, width=3)
        draw.arc((base_x + 44, base_y - 20, base_x + 128, base_y + 4), start=180, end=360, fill=(255, 230, 170), width=2)

    # 俄式风格尖顶/洋葱顶符号（几何化）
    dome_x = width - lamp_x - int(width * 0.14)
    dome_y = lamp_y + 20
    dome_w = max(96, int(width * 0.08))
    draw.ellipse((dome_x, dome_y, dome_x + dome_w, dome_y + 18), outline=accent, width=2, fill=(12, 9, 32))
    draw.polygon(
        [(dome_x + dome_w // 2, dome_y - 16), (dome_x + dome_w // 2 - 28, dome_y + 30), (dome_x + dome_w // 2 + 28, dome_y + 30)],
        fill=(17, 15, 45),
        outline=magenta,
    )
    for i in range(6):
        px = dome_x + 6 + i * (dome_w // 6)
        draw.line((px, dome_y + 26, px + 6, dome_y + 42), fill=magenta, width=2)
    draw.arc((dome_x + 8, dome_y - 8, dome_x + dome_w - 8, dome_y + 62), start=180, end=360, fill=magenta, width=4)
    draw.text((dome_x + 18, dome_y + 28), "MIR", font=body_font, fill=accent)
    for offset in (0, 1):
        x0 = width // 3 + 14 + (offset * 8)
        draw.line((x0, lamp_y - 4 + (offset * 4), width // 3 + width // 2 + 4, lamp_y - 4 + (offset * 4)), fill=(255, 235, 160), width=1 + offset)


def _draw_neon_glow_rect(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    rect: Tuple[int, int, int, int],
    fill: Tuple[int, int, int],
    border: Tuple[int, int, int],
    alpha: int = 90,
) -> None:
    x0, y0, x1, y1 = rect
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for i in range(4, 0, -1):
        gd.rounded_rectangle(
            (x0 - i * 3, y0 - i * 3, x1 + i * 3, y1 + i * 3),
            radius=22 + i,
            fill=None,
            outline=(border[0], border[1], border[2], max(12, alpha - i * 15)),
            width=1,
        )
    gd.rounded_rectangle((x0, y0, x1, y1), radius=18, fill=(fill[0], fill[1], fill[2], alpha))
    gd.rounded_rectangle((x0 + 1, y0 + 1, x1 - 1, y1 - 1), radius=18, outline=(border[0], border[1], border[2], 220), width=2)
    img.paste(Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB"))


def _draw_cyberpunk_style(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    design: DesignConfig,
    content: LockContent,
    title_font: ImageFont.FreeTypeFont,
    body_font: ImageFont.FreeTypeFont,
) -> None:
    title_color = _hex_to_rgb(design.title_color)
    body_color = _hex_to_rgb(design.body_color)
    accent = _hex_to_rgb(design.accent_color)
    deep = (12, 14, 24)
    panel = (24, 30, 54)
    steel = (132, 150, 182)
    steel_dark = (64, 78, 105)
    steel_edge = (222, 228, 250)
    electric = (178, 213, 255)
    magenta = (214, 190, 240)
    cyan = (186, 212, 255)
    gold = (230, 205, 138)
    edge_soft = (158, 173, 203)
    edge_mid = (86, 103, 146)
    now = datetime.now()

    border_alpha = 130
    hero_size = max(68, int(min(width, height) * 0.065))
    try:
        hero_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", hero_size)
    except (OSError, TypeError):
        hero_font = title_font
    banner_h = int(height * 0.15)
    banner = Image.new("RGBA", (width, banner_h), (0, 0, 0, 0))
    banner_draw = ImageDraw.Draw(banner)
    for i in range(banner_h):
        alpha = int(90 + 80 * (1 - i / max(1, banner_h)))
        banner_draw.rectangle((0, i, width, i + 1), fill=(8, 10, 28, alpha))
    banner_draw.rectangle(
        (0, 0, width, 7),
        fill=(246, 44, 204, 255),
    )
    banner_draw.rectangle(
        (0, 13, width, 17),
        fill=(58, 255, 250, 255),
    )
    banner_draw.rectangle(
        (int(width * 0.025), 6, width - int(width * 0.025), banner_h - 6),
        outline=(255, 224, 128, 210),
        width=2,
    )
    marker = "CYBERPUNK LOCKSCREEN V4"
    marker_w = banner_draw.textlength(marker, font=hero_font)
    marker_x = (width - int(marker_w)) // 2
    marker_y = int((banner_h - hero_size) * 0.46)
    banner_draw.text(
        (marker_x, marker_y),
        marker,
        font=hero_font,
        fill=(245, 250, 255, 236),
        stroke_fill=(24, 42, 96, 255),
        stroke_width=2,
    )
    banner_draw.text(
        (marker_x + int(hero_size * 0.52), marker_y + int(hero_size * 0.88)),
        _text_safe("赛博 + 东方俄式混合横屏增强版"),
        font=body_font,
        fill=(214, 200, 140, 236),
    )
    banner_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    banner_layer.paste(banner, (0, 0))
    img.paste(Image.alpha_composite(img.convert("RGBA"), banner_layer).convert("RGB"))

    # 顶层亚克力底纹
    shine = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sg = ImageDraw.Draw(shine)
    sg.ellipse(
        (int(width * 0.20), int(height * -0.08), int(width * 0.80), int(height * 0.46)),
        fill=(255, 255, 255, 7),
    )
    sg.rectangle((0, 0, width, int(height * 0.16)), fill=(255, 255, 255, 4))
    control_left = int(width * 0.06)
    control_top = int(height * 0.024)
    control_h = int(height * 0.12)
    control_right = width - control_left
    control_bottom = control_top + control_h
    sg.rounded_rectangle(
        (control_left, control_top, control_right, control_bottom),
        radius=0,
        fill=(8, 12, 30, 168),
        outline=(steel_edge[0], steel_edge[1], steel_edge[2], 172),
        width=2,
    )
    sg.rectangle(
        (control_left + 18, control_top + 18, control_left + 64, control_bottom - 18),
        outline=(gold[0], gold[1], gold[2], 170),
        width=2,
    )
    sg.text((control_left + 82, control_top + 19), _text_safe("VISUAL LOCK"), font=body_font, fill=(steel_edge[0], steel_edge[1], steel_edge[2], 230))
    sg.text(
        (control_left + 82, control_bottom - 44),
        _text_safe("CYBER HUD · ORIENTAL / RUSSIAN"),
        font=body_font,
        fill=(gold[0], gold[1], gold[2], 200),
    )
    for i in range(4):
        x = control_left + 14 + i * int((control_right - control_left - 28) / 3)
        sg.line((x, control_top + 2, x, control_bottom - 2), fill=(steel_edge[0], steel_edge[1], steel_edge[2], 90), width=1)
        sg.line((control_left + 2 + i * 16, control_top + 2, control_right - 2 - i * 16, control_top + 2), fill=(steel_edge[0], steel_edge[1], steel_edge[2], 65), width=1)
        sg.line((control_left + 2 + i * 16, control_bottom - 2, control_right - 2 - i * 16, control_bottom - 2), fill=(steel_edge[0], steel_edge[1], steel_edge[2], 65), width=1)
    sg.text((control_right - 232, control_top + 22), _text_safe("STATE: ACTIVE"), font=body_font, fill=(250, 250, 250))
    sg.text((control_right - 232, control_top + 22 + body_font.size + 4), now.strftime("%H:%M:%S"), font=title_font, fill=(200, 222, 255))
    img.paste(Image.alpha_composite(img.convert("RGBA"), shine).convert("RGB"))

    draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=0,
        outline=(cyan[0], cyan[1], cyan[2], border_alpha),
        width=5,
    )
    draw.rounded_rectangle((6, 6, width - 6, height - 6), radius=0, outline=steel_dark, width=2)
    draw.rounded_rectangle((12, 12, width - 12, height - 12), radius=0, outline=(steel_edge[0], steel_edge[1], steel_edge[2], 130), width=1)

    header_zone = int(height * 0.205)
    for i in range(12):
        yy = int(i * height / 11)
        alpha = max(8, 30 - i)
        draw.line((int(width * 0.04), yy, width - int(width * 0.04), yy), fill=(steel_dark[0], steel_dark[1], steel_dark[2], alpha), width=1)

    draw.rounded_rectangle(
        (int(width * 0.03), 0, width - int(width * 0.03), header_zone),
        radius=0,
        fill=(panel[0], panel[1], panel[2], 230),
    )
    draw.rounded_rectangle(
        (int(width * 0.035), header_zone, width - int(width * 0.035), height),
        radius=0,
        fill=(deep[0], deep[1], deep[2], 210),
    )

    for i in range(11):
        x = int(width * 0.05) + i * int(width * 0.083)
        draw.line((x, header_zone + 8, x, header_zone + 34), fill=(steel_edge[0], steel_edge[1], steel_edge[2], 110), width=1)

    for i in range(7):
        y = int(height * (0.18 + i * 0.11))
        x0 = int(width * 0.06)
        x1 = width - int(width * 0.06)
        seg = int(width * 0.045)
        for _x in range(x0, x1, seg):
            draw.line((_x, y, _x + int(seg * 0.6), y), fill=(steel[0], steel[1], steel[2], 28), width=1)

    _draw_eastern_russian_motif(
        draw=draw,
        width=width,
        height=height,
        accent=accent,
        electric=electric,
        magenta=magenta,
        title_font=title_font,
        body_font=body_font,
    )

    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for i in range(16):
        ratio = i / 15.0
        c = tuple(int(deep[j] + ((j + 1) * 2 + 16) * (1 - ratio * 0.6)) for j in range(3))
        alpha = max(8, 44 - i * 2)
        yy = int(height * 0.2 + i * (height * 0.038))
        ld.rounded_rectangle(
            (int(width * 0.04), yy, width - int(width * 0.04), yy + max(32, int(height * 0.038))),
            radius=0,
            fill=(c[0], c[1], c[2], alpha),
        )
    img.paste(Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB"))

    grid_color = _blend_color(steel, steel_edge, 0.55)
    _draw_grid_lines(draw, width, height, grid_color)
    _draw_scanline(draw, width, height, _blend_color(grid_color, panel, 0.55))

    pad_x = max(24, int(width * 0.025))
    pad_y = max(18, int(height * 0.022))
    _draw_neon_glow_rect(
        img,
        draw,
        (pad_x + 2, pad_y + 2, width - pad_x - 2, pad_y + int(height * 0.2) - 2),
        (12, 12, 24),
        steel_edge,
        alpha=72,
    )
    draw.rounded_rectangle(
        (pad_x, pad_y, width - pad_x, pad_y + int(height * 0.2)),
        radius=16,
        fill=(22, 24, 44, 220),
        outline=(steel_edge[0], steel_edge[1], steel_edge[2], 170),
        width=2,
    )
    # HUD 分隔框样式
    status_panel_h = int(height * 0.2)
    for i in range(4):
        yy = pad_y + 24 + i * int((status_panel_h - 42) / 3)
        draw.line((pad_x + 16, yy, width - pad_x - 16, yy), fill=(edge_soft[0], edge_soft[1], edge_soft[2], 45), width=1)
    for i in range(6):
        x = pad_x + 34 + i * int((width - pad_x * 2 - 68) / 5)
        draw.line((x, pad_y + 28, x, pad_y + int(height * 0.2) - 28), fill=(gold[0], gold[1], gold[2], 80), width=1)
    draw.line((pad_x + 3, pad_y + 3, width - pad_x - 3, pad_y + 3), fill=(steel_edge[0], steel_edge[1], steel_edge[2], 140), width=1)
    draw.line((pad_x + 3, pad_y + int(height * 0.2) - 3, width - pad_x - 3, pad_y + int(height * 0.2) - 3), fill=(steel_edge[0], steel_edge[1], steel_edge[2], 140), width=1)

    draw.text((pad_x + 22, pad_y + 14), now.strftime("%H:%M:%S"), font=title_font, fill=steel_edge)
    draw.text(
        (pad_x + 22, pad_y + 16 + title_font.size),
        f"{now.month:02d}/{now.day:02d} {_weekday_cn()}",
        font=body_font,
        fill=(steel[0], steel[1], steel[2], 220),
    )
    draw.text(
        (pad_x + 22, pad_y + 26 + title_font.size * 2),
        "LOCK READY · SECURE",
        font=body_font,
        fill=(steel_edge[0], steel_edge[1], steel_edge[2], 210),
    )
    badge_right = width - pad_x - 20
    draw.rounded_rectangle((badge_right - 250, pad_y + 26, badge_right, pad_y + 106), radius=8, fill=(13, 15, 28), outline=steel_edge, width=1)
    draw.rounded_rectangle((badge_right - 244, pad_y + 44, badge_right - 8, pad_y + 46), radius=1, fill=(gold[0], gold[1], gold[2], 200))
    draw.rounded_rectangle((badge_right - 244, pad_y + 76, badge_right - 8, pad_y + 78), radius=1, fill=(gold[0], gold[1], gold[2], 160))
    draw.text((badge_right - 234, pad_y + 54), "ALERT", font=body_font, fill=steel_edge)
    draw.text((badge_right - 242, pad_y + 84), "LOCK MODE", font=body_font, fill=(220, 230, 248))

    draw.text(
        (pad_x + 32, pad_y + int(height * 0.2) - 58),
        _text_safe("视觉识别锁屏 · 赛博风格"),
        font=title_font,
        fill=(255, 255, 255),
    )
    draw.text(
        (pad_x + 32, pad_y + int(height * 0.2) - 20),
        _text_safe("CYBER GRID HUD | 横屏优化版"),
        font=body_font,
        fill=(magenta[0], magenta[1], magenta[2]),
    )
    draw.text((pad_x + width // 3 + 24, pad_y + 24), _text_safe("清朝纹饰 · 俄式穹顶 · 金属密封层"), font=body_font, fill=(221, 208, 161))
    draw.text(
        (pad_x + width // 3 + 24, pad_y + int(height * 0.2) - 28),
        _text_safe("清朝宫灯 + 俄式穹顶纹样 · 双文化安全框架"),
        font=body_font,
        fill=(190, 205, 238),
    )

    card_margin = int(height * 0.21)
    card_w = min(width - pad_x * 2, int(width * 0.94))
    card_h = max(int(height * 0.56), int(height - card_margin - height * 0.10))
    card_x = pad_x
    card_y = card_margin
    _draw_neon_glow_rect(
        img,
        draw,
        (card_x, card_y, card_x + card_w, card_y + card_h),
        (17, 18, 35),
        steel_edge,
        alpha=110,
    )
    draw.rounded_rectangle(
        (card_x + 2, card_y + 2, card_x + card_w - 2, card_y + card_h - 2),
        radius=18,
        outline=(steel_edge[0], steel_edge[1], steel_edge[2], 150),
        width=1,
    )
    for i in range(2):
        line_y = card_y + 74 + i * int(card_h * 0.38)
        draw.line((card_x + 34, line_y, card_x + card_w - 34, line_y), fill=(steel[0], steel[1], steel[2], 60), width=1)

    # 卡片阴影与金属接缝
    _draw_neon_glow_rect(
        img,
        draw,
        (card_x, card_y, card_x + card_w, card_y + card_h),
        (7, 10, 24),
        edge_mid,
        alpha=44,
    )
    for i in range(7):
        yy = card_y + 96 + i * int((card_h - 140) / 6)
        alpha = 16 + (i % 2) * 6
        draw.line((card_x + 18, yy, card_x + card_w - 18, yy), fill=(edge_mid[0], edge_mid[1], edge_mid[2], alpha), width=1)

    cx = card_x + 28
    draw.text((cx, card_y + 26), design.headline, font=title_font, fill=(255, 255, 255))
    draw.text((cx, card_y + 26 + title_font.size + 10), design.subheadline, font=body_font, fill=steel_edge)
    draw.text((cx + 8, card_y + 26 + title_font.size * 2 + 42), _text_safe("清风纹理 · 宫灯穹顶 · 俄式花砖"), font=body_font, fill=gold)

    _draw_bar_graphics(draw, cx + 4, card_y + 118, card_w - 64, 96, steel, body_color)
    _draw_bar_graphics(draw, cx + 4, card_y + 206, card_w - 64, 76, steel, body_color)

    badge_y = card_y + 276
    draw.rounded_rectangle((cx, badge_y, cx + 18, badge_y + 18), radius=2, fill=magenta)
    draw.text((cx + 26, badge_y - 1), "NEO SECURITY GRID", font=body_font, fill=title_color)

    status_value = _text_safe(f"目标识别: {content.title if content.title else 'FACE DETECTED'}")
    draw.rounded_rectangle(
        (cx + int(card_w * 0.58), badge_y - 8, card_x + card_w - 28, badge_y + 38),
        radius=8,
        fill=(14, 16, 32),
        outline=steel_edge,
        width=1,
    )
    draw.text((cx + int(card_w * 0.58) + 14, badge_y + 4), status_value[:18], font=body_font, fill=(gold[0], gold[1], gold[2], 220))

    for i in range(2):
        draw.rounded_rectangle(
            (card_x + 34, card_y + card_h - 42, card_x + card_w - 34, card_y + card_h - 10),
            radius=10,
            outline=(body_color[0], body_color[1], body_color[2], 90),
            width=1,
        )
        draw.text(
            (card_x + 44 + (i * 150), card_y + card_h - 31),
            ["RUN: AI LOCK", "TIME: MONITORING"][i],
            font=body_font,
            fill=steel,
        )

    content_lines = [*content.lines] if content.lines else [_text_safe("检测到目标，安全图层已激活")]
    start_y = card_y + 326
    for idx, line in enumerate(content_lines[:7]):
        if not line:
            continue
        text_color = steel_edge if idx % 2 == 0 else body_color
        start_y = _draw_multiline(
            draw=draw,
            text=line,
            x=cx,
            y=start_y + idx * 8,
            width=card_w - 64,
            font=body_font,
            fill=text_color,
        )
    draw.rounded_rectangle(
        (card_x + 28, start_y + 20, card_x + card_w - 28, start_y + 98),
        radius=14,
        fill=(12, 14, 28),
        outline=steel_edge,
        width=1,
    )
    draw.text((card_x + 40, start_y + 36), _text_safe("STATUS: FACE LOCKED IN SIGHT"), font=body_font, fill=(230, 242, 255))
    draw.text((card_x + 40, start_y + 98), _text_safe("CYBERPUNK LOCKSCREEN V3"), font=body_font, fill=steel_edge)
    draw.line((card_x + 40, start_y + 58, card_x + card_w - 58, start_y + 58), fill=(edge_soft[0], edge_soft[1], edge_soft[2], 70), width=1)
    draw.line((card_x + 40, start_y + 73, card_x + card_w - 58, start_y + 73), fill=(edge_soft[0], edge_soft[1], edge_soft[2], 70), width=1)
    draw.text((card_x + 40, start_y + 62), _text_safe(f"AUTO RETURN: {getattr(design, 'auto_unlock_seconds', 12)}s"), font=body_font, fill=(steel_edge[0], steel_edge[1], steel_edge[2], 215))

    footer = _text_safe("VISUAL LOCKSCREEN · CYBERPUNK MODE · V3")
    footer_w = draw.textlength(footer, font=body_font)
    footer_x = width - pad_x - int(footer_w)
    draw.text((footer_x, height - pad_y - body_font.size), footer, font=body_font, fill=grid_color)
    marker = _text_safe("CYBERPUNK LOCKSCREEN V4 / CYBER | ORIENTAL / RUSSIAN")
    _title_mark, overlay = _pick_chinese_font(
        max(36, int(min(width, height) * 0.028)),
        max(24, int(min(width, height) * 0.018)),
    )
    marker_w = draw.textlength(marker, font=overlay)
    marker_x = max(18, (width - int(marker_w)) // 2)
    marker_y = 12 + int(body_font.size * 0.2)
    draw.text((marker_x, marker_y), marker, font=overlay, fill=(255, 210, 140, 245))
    draw.rectangle((0, 0, width, 6), fill=(246, 44, 204, 255))
    draw.rectangle((0, 10, width, 14), fill=(58, 255, 250, 255))


def _draw_wallpaper_style(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    design: DesignConfig,
    content: LockContent,
    title_font: ImageFont.FreeTypeFont,
    body_font: ImageFont.FreeTypeFont,
) -> None:
    title_color = _hex_to_rgb(design.title_color)
    body_color = _hex_to_rgb(design.body_color)
    accent = _hex_to_rgb(design.accent_color)
    muted = _blend_color(body_color, title_color, 0.5)
    base_color = _blend_color(_hex_to_rgb(design.bg_color_bottom), _hex_to_rgb(design.bg_color_top), 0.12)

    pad_x = max(24, int(width * 0.03))
    pad_y = max(20, int(height * 0.022))
    status_h = max(72, int(height * 0.11))
    now = datetime.now()

    _draw_frost_panel(img, draw, pad_x, pad_y, width - pad_x * 2, status_h, base_color, accent, radius=20, alpha=170)
    draw.rounded_rectangle((pad_x + 2, pad_y + 2, width - pad_x - 2, pad_y + status_h - 2), radius=18, outline=(255, 255, 255), width=1)

    draw.text((pad_x + 18, pad_y + 16), now.strftime("%H:%M"), font=title_font, fill=title_color)
    draw.text((pad_x + 18, pad_y + 18 + title_font.size), f"{now.month:02d}-{now.day:02d} {_weekday_cn()}", font=body_font, fill=body_color)
    draw.text((width - pad_x - 116, pad_y + 24), "SECURE", font=body_font, fill=title_color)

    cx = width // 2
    cy = int(height * 0.47)
    badge_r = max(46, int(min(width, height) * 0.05))
    _draw_lock_badge(draw, cx, cy, badge_r, accent, on=(255, 255, 255))

    content_lines = [content.title, content.subtitle, *content.lines]
    headline = content_lines[0] if content_lines else _text_safe("视觉锁屏已触发")
    subtitle = content_lines[1] if len(content_lines) > 1 else _text_safe("正在展示安全提示")
    detail_lines = (
        content_lines[2:]
        if len(content_lines) > 2
        else [
            _text_safe("检测到移动目标后自动进入锁屏"),
            _text_safe("请确认区域安全或轻触任意键返回"),
        ]
    )

    card_w = min(width - pad_x * 2, int(width * 0.86))
    card_h = max(int(height * 0.34), int((height - cy - 18) * 0.58))
    card_x = (width - card_w) // 2
    card_y = min(height - int(pad_y * 2), int(height * 0.58))
    _draw_frost_panel(img, draw, card_x, card_y, card_w, card_h, (12, 18, 34), accent, radius=24, alpha=185)
    draw.text((card_x + 22, card_y + 18), headline, font=title_font, fill=title_color)
    draw.text((card_x + 22, card_y + 18 + title_font.size + 8), subtitle, font=body_font, fill=muted)

    grouped = _group_content_lines(detail_lines)
    cursor_y = card_y + 22 + body_font.size + 26
    for title, lines in grouped[:4]:
        draw.rounded_rectangle(
            (card_x + 20, cursor_y - 8, card_x + card_w - 20, cursor_y + len(lines) * (body_font.size + 14)),
            radius=16,
            outline=(255, 255, 255, 90),
            width=1,
        )
        draw.text((card_x + 30, cursor_y), title, font=body_font, fill=title_color)
        cursor_y += body_font.size + 8
        for line in lines[:3]:
            cursor_y = _draw_multiline(
                draw=draw,
                text=line,
                x=card_x + 30,
                y=cursor_y + 4,
                width=card_w - 86,
                font=body_font,
                fill=body_color,
            ) + 10
        cursor_y += 10

    draw.text(
        (pad_x, height - pad_y - body_font.size),
        _text_safe("视觉识别锁屏 · 风格来源：i3lock-fancy / betterlockscreen"),
        font=body_font,
        fill=muted,
    )


def _draw_cyberpunk_emergency_banner(
    img: Image.Image,
    width: int,
    height: int,
    title_font: ImageFont.FreeTypeFont,
) -> None:
    banner = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(banner)
    d.rectangle((0, 0, width, 8), fill=(246, 44, 204, 255))
    d.rectangle((0, 14, width, 20), fill=(58, 255, 250, 255))
    d.rectangle((0, 35, width, 140), outline=(255, 224, 128, 230), width=3)
    marker = "CYBERPUNK LOCKSCREEN V4 (FORCED)"
    d.text((20, 44), marker, font=title_font, fill=(240, 252, 255, 250))
    d.text((20, 94), _text_safe("视觉识别锁屏 · 赛博横屏强化模式"), font=title_font, fill=(230, 205, 138, 245))
    img.paste(Image.alpha_composite(img.convert("RGBA"), banner).convert("RGB"))


def _draw_card(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    title: str,
    body_lines: List[str],
    title_font: ImageFont.FreeTypeFont,
    body_font: ImageFont.FreeTypeFont,
    title_color: Tuple[int, int, int],
    border_color: Tuple[int, int, int],
    body_color: Tuple[int, int, int],
) -> None:
    if w <= 0 or h <= 0:
        return
    draw.rounded_rectangle((x, y, x + w, y + h), radius=18, fill=(8, 16, 30), outline=border_color, width=2)
    draw.text((x + 16, y + 12), _text_safe(title), font=title_font, fill=title_color)
    iy = y + 48
    for line in body_lines:
        if not line:
            continue
        iy = _draw_multiline(
            draw=draw,
            text=line,
            x=x + 16,
            y=iy,
            width=w - 32,
            font=body_font,
            fill=body_color,
        )
        iy += 4


def _draw_multiline(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    width: int,
    font: ImageFont.FreeTypeFont,
    fill: Tuple[int, int, int],
) -> int:
    words = list(text)
    if not words:
        return y
    line = ""
    cy = y
    for word in words:
        # 对中文逐字换行，避免在无空格文本中超出卡片宽度。
        candidate = f"{line}{word}"
        if draw.textlength(candidate, font=font) <= width:
            line = candidate
            continue
        if line:
            draw.text((x, cy), line, font=font, fill=fill)
            line_height = _compat_font_line_height(font)
            cy += line_height + 6
        line = word
    if line:
        draw.text((x, cy), line, font=font, fill=fill)
        line_height = _compat_font_line_height(font)
        cy += line_height + 6
    return cy


def _group_content_lines(lines: List[str]) -> List[Tuple[str, List[str]]]:
    categories = {
        _text_safe("天气"): [],
        _text_safe("运势"): [],
        _text_safe("待办"): [],
        _text_safe("新闻"): [],
    }
    uncategorized = []
    for line in lines:
        placed = False
        for key in list(categories):
            prefix = f"{key}："
            if line.startswith(prefix):
                categories[key].append(line[len(prefix) :].strip())
                placed = True
                break
        if not placed:
            uncategorized.append(line)

    cards = []
    for title, body in categories.items():
        if body:
            cards.append((title, body))
    if uncategorized:
        cards.append((_text_safe("提示"), [*uncategorized]))
    return cards


def _draw_fallback_canvas(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    design: DesignConfig,
    content: LockContent,
    title_font: ImageFont.FreeTypeFont,
    body_font: ImageFont.FreeTypeFont,
) -> None:
    bg = (11, 18, 32)
    img.paste(bg, (0, 0, width, height))
    title_color = (225, 238, 255)
    body_color = (210, 223, 249)
    accent = (96, 165, 250)
    title_font = _pick_chinese_font(max(42, int(min(width, height) * 0.05)), max(30, int(min(width, height) * 0.03)))[0]
    body_font = _pick_chinese_font(42, 30)[1]

    pad_x = max(32, int(width * 0.04))
    base_y = max(36, int(height * 0.05))
    panel_bg = (14, 22, 38)
    panel_border = (65, 99, 196)
    panel_w = max(240, int(width * 0.9))
    panel_h = max(260, int(height * 0.9))
    panel_x0 = (width - panel_w) // 2
    panel_y0 = (height - panel_h) // 2
    panel_x1 = panel_x0 + panel_w
    panel_y1 = panel_y0 + panel_h
    draw.rounded_rectangle(
        (
            panel_x0,
            panel_y0,
            panel_x1,
            panel_y1,
        ),
        radius=26,
        fill=panel_bg,
        outline=panel_border,
        width=2,
    )
    draw.text((pad_x + 24, base_y + 24), _text_safe(content.title), font=title_font, fill=title_color)
    draw.text((pad_x + 24, base_y + 24 + title_font.size + 10), _text_safe(content.subtitle), font=body_font, fill=accent)
    marker_y = base_y + 24 + title_font.size + 56
    draw.line(
        (pad_x + 24, marker_y, width - pad_x - 24, marker_y),
        fill=accent,
        width=2,
    )

    info_lines = [content.title, content.subtitle, *(content.lines[:7] if content.lines else [])]
    y = marker_y + 24
    for line in info_lines:
        if not line:
            continue
        y = _draw_multiline(
            draw=draw,
            text=_text_safe(line),
            x=pad_x + 24,
            y=y,
            width=width - pad_x * 2 - 48,
            font=body_font,
            fill=body_color,
        )
        y += 12


def _draw_plain_text_canvas(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    design: DesignConfig,
    content: LockContent,
    title_font: ImageFont.FreeTypeFont,
    body_font: ImageFont.FreeTypeFont,
    auto_close_seconds: int,
    style: str = "plain",
) -> None:
    is_high_contrast = style in {"high_contrast", "text", "plain", "safe"}
    is_safe = style == "safe"

    if is_high_contrast or is_safe or style == "text":
        bg = (8, 12, 24)
        panel_fill = (14, 26, 46)
        panel_outline = (170, 220, 255)
        title_color = (250, 254, 255)
        subtitle_color = (186, 220, 255)
        body_color = (226, 238, 255)
        line_color = (95, 128, 166)
        footer_color = (135, 154, 175)
    else:
        bg = (240, 246, 255)
        panel_fill = (255, 255, 255)
        panel_outline = (13, 35, 67)
        title_color = (9, 24, 45)
        subtitle_color = (27, 48, 74)
        body_color = (30, 57, 90)
        line_color = (45, 70, 100)
        footer_color = (65, 92, 124)

    img.paste(bg, (0, 0, width, height))

    panel_margin = max(18, int(width * 0.02))
    panel_w = width - panel_margin * 2
    panel_h = min(height - panel_margin * 2, int(height * 0.94))
    panel_x = panel_margin
    panel_y = panel_margin

    draw.rounded_rectangle(
        (panel_x, panel_y, panel_x + panel_w, panel_y + panel_h),
        radius=16,
        fill=panel_fill,
        outline=panel_outline,
        width=2,
    )

    title = _text_safe(content.title) or design.headline
    subtitle = _text_safe(content.subtitle) or design.subheadline
    lines = [content.title, content.subtitle, *content.lines]
    lines = [_text_safe(line) for line in lines if _text_safe(line)]
    if not lines:
        lines = [
            "VISUAL LOCK",
            "检测触发",
            f"{datetime.now():%Y-%m-%d %H:%M:%S}",
            f"自动返回：{auto_close_seconds} 秒",
        ]

    title_size = max(56, int(min(width, height) * 0.058))
    body_size = max(34, int(min(width, height) * 0.032))
    if title_font.size != title_size or body_font.size != body_size:
        try:
            title_font = title_font.font_variant(size=title_size)
        except Exception:
            pass
        try:
            body_font = body_font.font_variant(size=body_size)
        except Exception:
            pass

    y = panel_y + 40
    content_area_w = panel_w - 48
    badge_height = 56
    draw.rounded_rectangle(
        (panel_x + 16, panel_y + 16, panel_x + panel_w - 16, panel_y + badge_height),
        radius=12,
        fill=(9, 22, 38),
        outline=panel_outline,
        width=2,
    )
    draw.text(
        (panel_x + 24, panel_y + 28),
        "视觉识别锁屏",
        font=body_font,
        fill=title_color,
    )

    y = panel_y + badge_height + 22
    y = _draw_multiline(
        draw=draw,
        text=title,
        x=panel_x + 24,
        y=y,
        width=content_area_w,
        font=title_font,
        fill=title_color,
    )
    y += 8
    y = _draw_multiline(
        draw=draw,
        text=subtitle,
        x=panel_x + 24,
        y=y,
        width=content_area_w,
        font=body_font,
        fill=subtitle_color,
    )
    y += 16
    draw.line((panel_x + 24, y, panel_x + panel_w - 24, y), fill=line_color, width=2)
    y += 26

    for idx, line in enumerate(lines[:16]):
        if y > panel_y + panel_h - 80:
            break
        if idx == 0:
            y += 6
        y = _draw_multiline(
            draw=draw,
            text=line,
            x=panel_x + 24,
            y=y,
            width=content_area_w,
            font=body_font,
            fill=body_color,
        )
        y += 10

    footer_y = panel_y + panel_h - 52
    draw.text(
        (panel_x + 24, footer_y),
        _text_safe(f"检测时间: {datetime.now():%H:%M:%S}"),
        font=body_font,
        fill=footer_color,
    )
    draw.text(
        (panel_x + panel_w - 320, footer_y),
        f"AUTO RETURN {auto_close_seconds}s",
        font=body_font,
        fill=footer_color,
    )


def render_lock_screen(
    design: DesignConfig,
    content: LockContent,
    output_path: Path,
    auto_close_seconds: int = 12,
) -> RenderResult:
    width, height = _resolve_canvas_size((1920, 1080))
    style = (design.style or "").strip().lower()
    force_style = os.environ.get("VISION_LOCK_FORCE_STYLE", "").strip().lower()
    if force_style in {"cyberpunk", "cyber", "github_wallpaper", "wallpaper", "i3lock_fancy", "fancy", "plain", "safe", "text", "high_contrast", "classic"}:
        if force_style != style:
            _append_log(f"render_style_forced from={style} to={force_style}")
        style = force_style
    _append_log(f"render_canvas width={width} height={height} style={style}")
    top = _hex_to_rgb(design.bg_color_top)
    bottom = _hex_to_rgb(design.bg_color_bottom)
    img = Image.new("RGBA", (width, height))
    _draw_gradient(img, top, bottom)

    draw = ImageDraw.Draw(img)
    base = min(width, height)
    if style in {"cyberpunk", "cyber"}:
        font_scale = 2.25
        title_size = max(40, int(base * 0.055 * font_scale))
        body_size = max(28, int(base * 0.028 * font_scale))
    else:
        font_scale = 1.0
        title_size = max(32, int(base * 0.048))
        body_size = max(24, int(base * 0.023))
    title_font, body_font = _pick_chinese_font(title_size, body_size)
    simple_render_env = os.environ.get("VISION_LOCK_SIMPLE_RENDER", "0").strip().lower() in {"1", "true", "yes", "y", "on"}
    plain_style_set = {"plain", "safe", "text", "high_contrast"}
    simple_render = simple_render_env and style not in {"cyberpunk", "cyber"} and style not in plain_style_set
    safe_content = LockContent(
        title=_text_safe(content.title),
        subtitle=_text_safe(content.subtitle),
        lines=[_text_safe(line) for line in content.lines],
    )

    if simple_render:
        _append_log("render_mode=simple")
        _draw_fallback_canvas(
            img=img,
            draw=draw,
            width=width,
            height=height,
            design=design,
            content=safe_content,
            title_font=title_font,
            body_font=body_font,
        )
        _save_image_atomically(img, output_path)
        return RenderResult(output_path)

    if style in {"plain", "safe", "text", "high_contrast"}:
        _draw_plain_text_canvas(
            img=img,
            draw=draw,
            width=width,
            height=height,
            design=design,
            content=safe_content,
            title_font=title_font,
            body_font=body_font,
            style=style,
            auto_close_seconds=auto_close_seconds,
        )
        _save_image_atomically(img, output_path)
        return RenderResult(output_path)

    try:
        if style in {"cyberpunk", "cyber"}:
            from vision_lock.cyber_clean import render as _cyber_render
            img = _cyber_render(width, height, content.title, content.subtitle)
            _save_image_atomically(img, output_path)
            return RenderResult(output_path)
        elif style in {"github_wallpaper", "wallpaper", "i3lock_fancy", "fancy"}:
            _draw_wallpaper_style(
                img=img,
                draw=draw,
                width=width,
                height=height,
                design=design,
                content=safe_content,
                title_font=title_font,
                body_font=body_font,
            )
        else:
            y = max(48, int(height * 0.06))
            accent = _hex_to_rgb(design.accent_color)
            title_color = _hex_to_rgb(design.title_color)
            body_color = _hex_to_rgb(design.body_color)
            subtle = (152, 166, 186)

            pad_x = max(36, int(width * 0.038))
            header_h = int(height * 0.24)
            if width > pad_x * 2 and header_h > 0:
                draw.rounded_rectangle((pad_x, int(height * 0.03), max(pad_x + 1, width - pad_x), header_h), radius=22, fill=(11, 21, 36), outline=accent, width=1)
                draw.text((pad_x + 36, int(height * 0.055)), safe_content.title, font=title_font, fill=title_color)
                draw.text((pad_x + 36, int(height * 0.095)), safe_content.subtitle, font=body_font, fill=accent)
                _draw_multiline(
                    draw=draw,
                    text=_text_safe(f"{auto_close_seconds}秒后自动关闭"),
                    x=width - pad_x - int(base * 0.4),
                    y=int(height * 0.06),
                    width=int(base * 0.36),
                    font=body_font,
                    fill=body_color,
                )

                cards = _group_content_lines(safe_content.lines)
                cards = cards[:4]
                y = int(height * 0.3)
                gap = int(width * 0.015)
                rows = (len(cards) + 1) // 2
                reserved_bottom = max(56, int(height * 0.04)) + int(height * 0.08)
                max_card_height = max(96, (height - y - reserved_bottom - gap * max(rows - 1, 0)) // max(rows, 1)) if rows else 0
                card_h = min(int(height * 0.26), max_card_height)
                card_w = max(140, (width - pad_x * 2 - gap) // 2)
                left_x = pad_x
                right_x = pad_x + card_w + gap
                for idx, (title, lines) in enumerate(cards[:4]):
                    ix = idx % 2
                    iy = idx // 2
                    x = left_x if ix == 0 else right_x
                    yy = y + iy * (card_h + int(height * 0.028))
                    _draw_card(
                        draw,
                        x,
                        yy,
                        card_w,
                        card_h,
                        title,
                        lines[:4],
                        title_font=body_font,
                        body_font=body_font,
                        title_color=title_color,
                        border_color=accent,
                        body_color=subtle,
                    )

                hint_y = height - max(56, int(height * 0.04))
                _draw_multiline(
                    draw=draw,
                    text=_text_safe("检测触发 · 人形识别通过后展示锁屏信息 | 任意键盘操作返回"),
                    x=pad_x,
                    y=hint_y,
                    width=width - pad_x * 2,
                    font=body_font,
                    fill=body_color,
                )
    except Exception as exc:
        _append_log(f"render_style_error style={style} err={type(exc).__name__}:{exc}")
        if style in {"cyberpunk", "cyber"}:
            _draw_cyberpunk_emergency_banner(img=img, width=width, height=height, title_font=title_font)
        else:
            _draw_fallback_canvas(
                img=img,
                draw=draw,
                width=width,
                height=height,
                design=design,
                content=safe_content,
                title_font=title_font,
                body_font=body_font,
            )

    _save_image_atomically(img, output_path)
    return RenderResult(output_path)


def _run_i3lock(command: Sequence[str], image_path: Path) -> int:
    cmd = [part.replace("%IMAGE%", str(image_path)) for part in command]
    cmd = [part.replace("{{IMAGE}}", str(image_path)) for part in cmd]
    cmd = [c for c in cmd if c]
    return subprocess.call(cmd)


def _run_i3lock_with_timeout(command: Sequence[str], image_path: Path, auto_unlock_seconds: int) -> int:
    cmd = [part.replace("%IMAGE%", str(image_path)) for part in command]
    cmd = [part.replace("{{IMAGE}}", str(image_path)) for part in cmd]
    cmd = [c for c in cmd if c]
    _append_log(f"lock_i3lock_cmd timeout={auto_unlock_seconds} cmd={' '.join(cmd)}")

    if auto_unlock_seconds <= 0:
        return subprocess.call(cmd)

    proc = subprocess.Popen(cmd)
    timer: threading.Timer | None = None
    try:
        timed_out = threading.Event()

        def _timeout_kill() -> None:
            if proc.poll() is None:
                timed_out.set()
                proc.terminate()

        timer = threading.Timer(auto_unlock_seconds, _timeout_kill)
        timer.start()
        rc = proc.wait()
        if rc is None:
            rc = -1
        if timed_out.is_set():
            _append_log("lock_i3lock_timeout_kill")
            return 0
        return rc
    finally:
        if timer is not None:
            timer.cancel()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(1.0)
            except subprocess.TimeoutExpired:
                proc.kill()


def _run_overlay(image_path: Path, auto_close: int) -> int:
    this_dir = Path(__file__).resolve().parent
    entry = this_dir / "overlay.py"
    return subprocess.call(
        [
            sys.executable,
            str(entry),
            "--image",
            str(image_path),
            "--seconds",
            str(auto_close),
        ]
    )


def execute_lock(design: DesignConfig, lock_cfg: LockConfig, content: LockContent, fallback_mode: str = "overlay") -> int:
    image_path = Path(lock_cfg.image_path)
    rendered = render_lock_screen(
        design,
        content,
        image_path,
        auto_close_seconds=lock_cfg.auto_unlock_seconds,
    )
    _append_log(f"lock_render_ok image={rendered.image_path} exists={rendered.image_path.exists()}")

    force_backend = os.environ.get("VISION_LOCK_FORCE_BACKEND", "").strip().lower()
    force_i3lock = os.environ.get("VISION_LOCK_FORCE_I3LOCK", "").strip().lower() in {"1", "true", "yes", "on", "y"}
    forced_backend = ""
    if force_backend in {"overlay", "i3lock", "auto"}:
        _append_log(f"lock_backend_forced env={force_backend}")
        forced_backend = force_backend

    if force_i3lock:
        _append_log("lock_backend_forced env=VISION_LOCK_FORCE_I3LOCK")
        forced_backend = "i3lock"

    backend = (forced_backend or lock_cfg.mode or fallback_mode).lower()
    if backend == "i3lock":
        if lock_cfg.i3lock_command is None:
            raise RuntimeError("i3lock 模式缺少 i3lock_command 配置")
        rc = _run_i3lock_with_timeout(lock_cfg.i3lock_command, image_path, lock_cfg.auto_unlock_seconds)
        _append_log(f"lock_i3lock rc={rc}")
        if rc != 0:
            _append_log("lock_i3lock_failed_fallback_overlay")
            rc = _run_overlay(image_path, lock_cfg.auto_unlock_seconds)
            _append_log(f"lock_overlay_fallback_i3lock rc={rc}")
        return rc

    if backend == "overlay":
        rc = _run_overlay(image_path, lock_cfg.auto_unlock_seconds)
        _append_log(f"lock_overlay rc={rc}")
        if rc != 0 and lock_cfg.i3lock_command:
            _append_log("lock_overlay_failed_fallback_i3lock")
            rc = _run_i3lock(lock_cfg.i3lock_command, image_path)
            _append_log(f"lock_i3lock_fallback rc={rc}")
        return rc

    # auto: default to overlay, then fallback to i3lock for compatibility.
    rc = _run_overlay(image_path, lock_cfg.auto_unlock_seconds)
    _append_log(f"lock_overlay rc={rc}")
    if rc != 0 and lock_cfg.i3lock_command:
        _append_log("lock_auto_failed_fallback_i3lock")
        rc = _run_i3lock(lock_cfg.i3lock_command, image_path)
        _append_log(f"lock_i3lock_fallback rc={rc}")
    return rc
