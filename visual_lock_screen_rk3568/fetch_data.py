#!/opt/visual_lock_screen_rk3568/.venv/bin/python3
"""Fetch weather (Chinese + UV), news, rates → lockscreen cache."""
import urllib.request, json, xml.etree.ElementTree as ET
import os, time, math, hashlib, socket, subprocess as sp
from pathlib import Path
from urllib.parse import quote
from datetime import datetime, timedelta

CACHE = Path("/tmp/lockscreen_data.json")
LOCKSCREEN_CACHE = Path("/tmp/visual_lock_screen_cache.json")
socket.setdefaulttimeout(8)

WEATHER_CN = {
    "Sunny":"晴","Clear":"晴","Partly cloudy":"多云","Cloudy":"多云","Overcast":"阴",
    "Mist":"雾","Fog":"雾","Haze":"霾","Light rain":"小雨","Moderate rain":"中雨",
    "Heavy rain":"大雨","Light drizzle":"小雨","Light rain shower":"小雨","Moderate or heavy rain shower":"中雨",
    "Torrential rain shower":"暴雨","Patchy rain possible":"阵雨","Light rain with thunder":"雷阵雨",
    "Thunderstorm":"雷暴","Light snow":"小雪","Moderate snow":"中雪","Heavy snow":"大雪",
    "Patchy snow possible":"阵雪","Light snow showers":"阵雪","Blizzard":"暴雪",
    "Rain":"雨","Snow":"雪","Rain Shower":"阵雨","Thunder":"雷",
    "Light sleet":"冻雨","Moderate or heavy sleet":"冻雨","Patchy light drizzle":"毛毛雨",
    "Freezing fog":"冻雾","Ice pellets":"冰雹",
}

def get_location():
    for url in ["https://ipapi.co/json/"]:
        try:
            r = urllib.request.urlopen(url, timeout=5)
            d = json.loads(r.read().decode())
            c = d.get("city","") or d.get("region","")
            lat = d.get("latitude") or d.get("lat") or 59.9
            lon = d.get("longitude") or d.get("lng") or 30.3
            if c:
                # Translate to Chinese
                CITY_CN = {"Saint Petersburg":"圣彼得堡","St Petersburg":"圣彼得堡","Moscow":"莫斯科"}
                c = CITY_CN.get(c, c)
                return c, float(lat), float(lon)
        except: pass
    # Translate city names to Chinese
    CITY_CN = {
        "Saint Petersburg": "圣彼得堡",
        "St Petersburg": "圣彼得堡",
        "Moscow": "莫斯科",
        "Saint-Peterburg": "圣彼得堡",
        "Sankt-Peterburg": "圣彼得堡",
        "Leningrad": "圣彼得堡",
        "Petrograd": "圣彼得堡",
    }
    return CITY_CN.get("Saint Petersburg", "圣彼得堡"), 59.93, 30.34

def get_weather_json(city):
    """Get weather via wttr.in simple format (fast, includes UV)."""
    try:
        url = f"http://wttr.in/{quote(city)}?format=%t+%C+%h+%w+%u"
        r = sp.run(["curl", "-s", "--connect-timeout", "5", url],
                   capture_output=True, text=True, timeout=10)
        if r.returncode != 0 or not r.stdout:
            raise RuntimeError(f"curl failed: {r.stderr[:100]}")
        parts = r.stdout.strip().split()
        temp = ""; desc_en = ""; humidity = ""; wind = ""; uv = "0"
        for p in parts:
            if p.startswith("+"): temp = p
            elif p.endswith("%"): humidity = p.rstrip("%")
            elif "km/h" in p: wind = p.replace("←","").replace("→","").replace("↑","").replace("↓","")
            elif p.isdigit(): uv = p
            else: desc_en = (desc_en + " " + p).strip()
        # Translate to Chinese
        desc_cn = desc_en
        WEATHER_CN = {"Sunny":"晴","Clear":"晴","Partly cloudy":"多云","Cloudy":"多云","Overcast":"阴",
            "Mist":"雾","Fog":"雾","Haze":"霾","Light rain":"小雨","Moderate rain":"中雨",
            "Heavy rain":"大雨","Light drizzle":"小雨","Light rain shower":"小雨",
            "Moderate or heavy rain shower":"中雨","Torrential rain shower":"暴雨",
            "Patchy rain possible":"阵雨","Light rain with thunder":"雷阵雨",
            "Thunderstorm":"雷暴","Rain With Thunderstorm":"雷暴","Light snow":"小雪",
            "Moderate snow":"中雪","Heavy snow":"大雪","Patchy snow possible":"阵雪",
            "Light snow showers":"阵雪","Blizzard":"暴雪","Rain":"雨","Snow":"雪",
            "Rain Shower":"阵雨","Thunder":"雷","Light sleet":"冻雨",
            "Moderate or heavy sleet":"冻雨","Patchy light drizzle":"毛毛雨",
            "Freezing fog":"冻雾","Ice pellets":"冰雹",
        }
        for en, cn in WEATHER_CN.items():
            if en.lower() in desc_en.lower():
                desc_cn = cn
                break
        if not temp:
            raise RuntimeError(f"Parse failed from: {r.stdout[:100]}")
        return temp, desc_cn, humidity, wind, uv
    except Exception as e:
        print(f"Weather fetch error: {e}")
        return None, None, None, None, None
def get_advice(temp_str, desc):
    try:
        t = int(temp_str.replace("+","").replace("°C","").replace("°","").split("(")[0])
        if temp_str.startswith("-"): t = -t
    except:
        try: t = int(temp_str)
        except: return ""
    dl = desc.lower()
    a = ""
    if "雨" in desc: a = "降雨提醒：请带伞"
    elif "雪" in desc: a = "降雪提醒：注意安全"
    elif "雾" in desc or "霾" in desc: a = "雾霾提醒：戴口罩"
    if t <= -10: c = "羽绒服+保暖内衣"
    elif t <= 0: c = "羽绒服"
    elif t <= 10: c = "大衣/厚外套"
    elif t <= 20: c = "夹克/卫衣"
    elif t <= 30: c = "短袖"
    else: c = "短袖+防暑"
    return (a+"，"+c) if a else c

def get_uv_advice(uv):
    try:
        u = float(uv)
        if u <= 2: return "紫外线：低，无需防护"
        elif u <= 5: return "紫外线：中等，涂防晒霜"
        elif u <= 7: return "紫外线：高，SPF30+防晒"
        elif u <= 10: return "紫外线：很高，减少外出"
        else: return "紫外线：极高，避免外出"
    except: return "紫外线：--"

def calc_sunset(lat, lon):
    try:
        now = datetime.utcnow()
        doy = now.timetuple().tm_yday
        p = math.asin(0.39795*math.cos(0.2163108+2*math.atan(0.9671396*math.tan(0.00860*(doy-186)))))
        ha = math.acos((math.sin(-0.0145)-math.sin(math.radians(lat))*math.sin(p))/(math.cos(math.radians(lat))*math.cos(p)))
        m = 720-4*(lon+math.degrees(ha))
        return (datetime.utcnow().replace(hour=0,minute=0)+timedelta(minutes=m)).strftime("%H:%M")
    except: return "--:--"

FORTUNES = ["今日宜创新","紫气东来","今日运势上佳","宜静不宜动","心想事成",
            "今日有贵人相助","运势平稳","宜出行","今日财运不错","好事多磨",
            "桃花运旺盛","宜学习新技能","今日宜放松","运势上升","宜早睡早起"]

def fetch():
    city, lat, lon = get_location()
    temp, desc, humidity, wind, uv = get_weather_json(city)
    if temp is None:
        temp, desc, humidity, wind, uv = "--", "--", "--", "--", "0"

    advice = get_advice(temp, desc) if temp != "--" else ""
    uv_advice = get_uv_advice(uv)

    # Exchange rate with yesterday comparison
    RATE_HISTORY = Path("/tmp/rate_history.json")
    rate = "--"
    rate_change = ""
    rate_direction = ""
    try:
        r = urllib.request.urlopen("https://api.exchangerate-api.com/v4/latest/CNY", timeout=35)
        d = json.loads(r.read().decode())
        rate = f"{float(d.get('rates',{}).get('RUB',0)):.2f}"
        today_val = float(rate)

        # Load yesterday's rate from history
        yesterday_rate = None
        yesterday_str = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        if RATE_HISTORY.exists():
            try:
                hist = json.loads(RATE_HISTORY.read_text())
                yesterday_rate = hist.get(yesterday_str)
            except:
                pass

        # Compare and calculate change
        if yesterday_rate and yesterday_rate > 0:
            diff = today_val - yesterday_rate
            pct = (diff / yesterday_rate) * 100
            if abs(diff) < 0.01:
                rate_direction = "\u2192"
                rate_change = "持平"
            elif diff > 0:
                rate_direction = "\u2191"
                rate_change = f"+{diff:.2f} (+{pct:.2f}%)"
            else:
                rate_direction = "\u2193"
                rate_change = f"{diff:.2f} ({pct:.2f}%)"

        # Save today's rate for tomorrow's comparison
        hist = {}
        if RATE_HISTORY.exists():
            try:
                hist = json.loads(RATE_HISTORY.read_text())
            except:
                pass
        hist[datetime.utcnow().strftime("%Y-%m-%d")] = today_val
        # Keep only last 30 days
        hist = dict(sorted(hist.items())[-30:])
        RATE_HISTORY.write_text(json.dumps(hist))
    except: pass

    # Russian news in Chinese
    news_items = []
    try:
        r = sp.run(["curl","-sL","--connect-timeout","10","https://www.epochtimes.com/gb/nsc413.htm/feed"],
                   capture_output=True, text=True, timeout=35)
        if r.stdout:
            root = ET.fromstring(r.stdout.encode("utf-8"))
            for item in root.findall(".//item")[:6]:
                t = item.find("title")
                if t is not None and t.text:
                    news_items.append(t.text)
    except: pass

    fortune = FORTUNES[int(hashlib.md5(time.strftime("%Y-%m-%d").encode()).hexdigest(),16)%len(FORTUNES)]
    sunset = calc_sunset(lat, lon)
    bj = (datetime.utcnow()+timedelta(hours=8)).strftime("%H:%M")

    data = {
        "weather": {"temp": temp, "desc": desc, "city": city,
                    "humidity": humidity+"%", "wind": wind, "uv": uv},
        "rate": rate, "rate_change": rate_change, "rate_direction": rate_direction, "advice": advice, "uv_advice": uv_advice,
        "news": news_items, "fortune": fortune,
        "sunset": sunset, "beijing": bj,
    }
    CACHE.write_text(json.dumps(data, ensure_ascii=False))

    # Format lines for lockscreen (vision_lock reads this format)
    lines = []
    lines.append(f"🌤 {desc} | 🌡 {temp}°C | 💧 {humidity}% | 🌬 {wind}km/h")
    lines.append(f"☀️ {uv_advice} | 🌅 日落 {sunset}")
    rate_line = f"💶 1 CNY = {rate} RUB"
    if rate_change and rate_direction:
        rate_line += f" {rate_direction} {rate_change}"
    lines.append(f"{rate_line} | 🎯 {fortune}")
    lines.append(f"👕 {advice}" if advice else "")
    for title in news_items[:3]:
        lines.append(f"📰 {title[:50]}")
    lines.append(f"🕐 北京时间 {bj} | 📍 {city}")

    LOCKSCREEN_CACHE.write_text(json.dumps({"lines": lines}, ensure_ascii=False))
    print(f"OK: {city} {temp}°C {desc} UV={uv} Rate={rate} News={len(news_items)}")

if __name__ == "__main__":
    fetch()