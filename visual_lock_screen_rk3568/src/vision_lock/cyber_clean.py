#!/opt/visual_lock_screen_rk3568/.venv/bin/python3
"""Lock screen v8: aligned weather+news, top-left rate, tech font."""
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
import json
from pathlib import Path

DATA_FILE = Path("/tmp/lockscreen_data.json")

def _load_data():
    if DATA_FILE.exists():
        try: return json.loads(DATA_FILE.read_text())
        except: pass
    return {"weather":{"temp":"--","desc":"--","city":"--"},"rate":"--","advice":"","news":[],"fortune":"","sunset":"--:--","beijing":"--:--"}

def _get_palette(hour):
    if 5 <= hour < 7:   return ((80,60,100),(200,120,80),(255,180,100),(255,150,100,12))
    elif 7 <= hour < 11: return ((100,120,180),(180,200,220),(100,200,255),(255,220,180,8))
    elif 11 <= hour < 14: return ((80,140,200),(160,200,240),(80,200,255),(255,240,200,5))
    elif 14 <= hour < 17: return ((60,100,160),(140,160,200),(60,180,230),(255,200,150,8))
    elif 17 <= hour < 19: return ((120,60,80),(200,100,60),(255,120,80),(255,160,100,15))
    elif 19 <= hour < 21: return ((40,30,70),(80,60,100),(200,100,150),(200,120,150,10))
    else:                return ((10,10,40),(25,20,60),(100,60,150),(150,100,180,6))

def render(width, height, headline, subheadline):
    data = _load_data()
    now = datetime.now()
    hour = now.hour
    top_c, bot_c, accent, glow_c = _get_palette(hour)

    img = Image.new("RGB", (width, height), (10, 12, 28))
    draw = ImageDraw.Draw(img)

    try:
        time_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 100)
        temp_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 72)
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 64)
        body_font = ImageFont.truetype("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 30)
        news_font = ImageFont.truetype("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 34)
        advice_font = ImageFont.truetype("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 42)
        corner_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 28)
        label_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 24)
    except:
        time_font = temp_font = title_font = body_font = news_font = advice_font = label_font = corner_font = ImageFont.load_default()

    # Gradient background
    for y in range(height):
        ratio = y / height
        r = int(top_c[0] + (bot_c[0] - top_c[0]) * ratio)
        g = int(top_c[1] + (bot_c[1] - top_c[1]) * ratio)
        b = int(top_c[2] + (bot_c[2] - top_c[2]) * ratio)
        draw.rectangle((0, y, width, y+1), fill=(max(5,r), max(5,g), max(5,b)))

    # Center glow
    glow = Image.new("RGBA", (width, height), (0,0,0,0))
    gd = ImageDraw.Draw(glow)
    cx, cy = width//2, height//2
    gr, gg, gb, ga = glow_c
    for r in range(350, 0, -10):
        alpha = max(1, ga - r // 35)
        gd.ellipse((cx-r, cy-r*2//3, cx+r, cy+r*2//3), fill=(gr, gg, gb, alpha))
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, width, 3), fill=accent)


    # TIME (center top)
    draw.text((width//2, int(height*0.10)), now.strftime("%H:%M"), font=time_font, fill=(255,255,255), anchor="mt", stroke_width=2, stroke_fill=(40,40,70))
    draw.text((width//2, int(height*0.10)+100), now.strftime("%Y-%m-%d"), font=label_font, fill=(200,200,220), anchor="mt")

    # WELCOME HEADLINE
    wy = int(height * 0.36)
    draw.text((width//2, wy), headline, font=title_font, fill=(255,255,255), anchor="mt", stroke_width=2, stroke_fill=(60,40,100))

    # ── LEFT + RIGHT panels (aligned at same Y) ──
    left_x = int(width * 0.06)
    panel_y = int(height * 0.48)
    right_x = int(width * 0.48)

    # LEFT: Weather
    w = data["weather"]
    draw.text((left_x, panel_y), w["temp"], font=temp_font, fill=(255,255,255))
    draw.text((left_x, panel_y+80), w["desc"], font=body_font, fill=(220,220,240))
    draw.text((left_x, panel_y+118), w["city"], font=label_font, fill=(160,160,180))
    draw.text((left_x, panel_y+148), "Beijing " + data.get("beijing","--:--") + "  ·  Sunset " + data.get("sunset","--:--"), font=corner_font, fill=(255,255,255))
    # Exchange rate with colored triangle indicator
    rate_num = data.get("rate", "--")
    rate_dir = data.get("rate_direction", "")
    tri_x = left_x + 2
    tri_y = panel_y + 178
    tri_s = 14  # triangle size
    if rate_dir == "↑":
        tri_color = (100, 255, 130)
        # upward triangle ▲
        draw.polygon([(tri_x, tri_y+tri_s), (tri_x+tri_s, tri_y+tri_s), (tri_x+tri_s//2, tri_y)], fill=tri_color)
    elif rate_dir == "↓":
        tri_color = (255, 100, 110)
        # downward triangle ▼
        draw.polygon([(tri_x, tri_y), (tri_x+tri_s, tri_y), (tri_x+tri_s//2, tri_y+tri_s)], fill=tri_color)
    else:
        tri_color = (160, 160, 180)
    # Draw rate text to the right of triangle
    rate_text = "CNY/RUB " + rate_num
    draw.text((left_x + tri_s + 10, panel_y+170), rate_text, font=temp_font, fill=(255,255,255))

    # Advice — weather tips below rate with generous spacing
    if data.get("advice"):
        adv_y = panel_y + 290
        adv_text = data["advice"]
        adv_w = draw.textlength(adv_text, font=advice_font)
        pad_x, pad_y = 14, 10
        draw.rounded_rectangle(
            (left_x - pad_x, adv_y - pad_y, left_x + adv_w + pad_x, adv_y + 50),
            radius=12, fill=(20, 20, 40))
        draw.text((left_x, adv_y), adv_text, font=advice_font,
                  fill=(255, 240, 80), stroke_width=1, stroke_fill=(80, 60, 20))

    # RIGHT: Fortune + News (aligned with left panel)
    if data.get("fortune"):
        draw.text((right_x, panel_y-28), "  " + data["fortune"], font=advice_font, fill=(255,220,180))
    draw.text((right_x, panel_y-2), "── 新闻 ──", font=label_font, fill=accent+(180,))
    for i, item in enumerate(data["news"][:7]):
        text = item[:50]
        draw.text((right_x, panel_y+30+i*52), text, font=news_font, fill=(230,230,245))

    # BOTTOM status
    by = int(height * 0.94)
    draw.text((width//2, by), "锁屏中 · 已锁定", font=label_font, fill=(160,160,180), anchor="mt")

    return img