#!/opt/visual_lock_screen_rk3568/.venv/bin/python3
"""Fetch all lock screen data including sunrise/sunset."""
import urllib.request, json, xml.etree.ElementTree as ET
import os, time, math, subprocess as sp, hashlib
from pathlib import Path
from urllib.parse import quote
from datetime import datetime, timedelta

CACHE = Path("/tmp/lockscreen_data.json")
FORTUNES = [
    "今日宜创新，不宜守旧。", "紫气东来，万事如意。", "今日运势上佳，适合社交。",
    "宜静不宜动，静待佳音。", "心想事成，诸事顺遂。", "今日有贵人相助，把握机会。",
    "运势平稳，适合处理积压事务。", "宜出行，宜会友，吉。", "今日财运不错，适合理财规划。",
    "保持耐心，好事多磨。", "桃花运旺盛，适合表达心意。", "宜学习新技能，收获颇丰。",
    "今日宜放松，不宜操之过急。", "运势上升，抓住机遇。", "宜早睡早起，保持好状态。",
]

def get_location():
    for url in ["https://ipapi.co/json/", "https://ipinfo.io/json/"]:
        try:
            r = urllib.request.urlopen(url, timeout=5)
            d = json.loads(r.read().decode())
            city = d.get("city", "")
            lat = d.get("latitude") or d.get("lat")
            lon = d.get("longitude") or d.get("lng")
            if city:
                return city, float(lat or 59.9), float(lon or 30.3)
        except: pass
    return "Shenzhen", 22.5, 114.0

def calc_sunset(lat, lon):
    try:
        now = datetime.utcnow()
        doy = now.timetuple().tm_yday
        p = math.asin(0.39795 * math.cos(0.2163108 + 2 * math.atan(0.9671396 * math.tan(0.00860 * (doy - 186)))))
        hour_angle = math.acos(
            (math.sin(-0.0145) - math.sin(math.radians(lat)) * math.sin(p)) /
            (math.cos(math.radians(lat)) * math.cos(p))
        )
        sunset_utc_min = 720 - 4 * (lon + math.degrees(hour_angle))
        sunset_utc = timedelta(minutes=sunset_utc_min)
        return (datetime.utcnow().replace(hour=0,minute=0,second=0,microsecond=0) + sunset_utc).strftime("%H:%M")
    except:
        return "--:--"

def get_weather_advice(temp_str, desc):
    try:
        temp_val = int(temp_str.replace("+","").replace("\u2013","").replace("\u00b0C","").replace("\u00b0","").split("(")[0])
        if temp_str.startswith("-"): temp_val = -temp_val
    except: return ""
    dl = desc.lower()
    advice = ""
    if any(w in dl for w in ["rain","shower","drizzle","thunder"]): advice = "降雨提醒：出门请带伞"
    elif any(w in dl for w in ["snow","sleet","blizzard"]): advice = "降雪提醒：注意出行安全"
    elif any(w in dl for w in ["fog","mist","haze"]): advice = "雾天提醒：能见度低"
    if temp_val <= -10: clothes = "羽绒服+保暖内衣"
    elif temp_val <= 0: clothes = "羽绒服"
    elif temp_val <= 10: clothes = "大衣/厚外套"
    elif temp_val <= 20: clothes = "夹克/卫衣"
    elif temp_val <= 30: clothes = "短袖/单衣"
    else: clothes = "短袖，防暑"
    return advice + "，" + clothes if advice else clothes

def fetch():
    city, lat, lon = get_location()
    data = {"weather":{"temp":"--","desc":"--","city":city},"rate":"--","advice":"","news":[],"fortune":"","sunset":"--:--","beijing":"--:--"}
    try:
        url = f"http://wttr.in/{quote(city)}?format=%t+%C&lang=zh"
        r = urllib.request.urlopen(url, timeout=8)
        t = r.read().decode().strip().split(" ", 1)
        data["weather"]["temp"] = t[0] if t else "--"
        data["weather"]["desc"] = t[1] if len(t)>1 else ""
        data["weather"]["city"] = city
        data["advice"] = get_weather_advice(t[0], t[1] if len(t)>1 else "")
    except: pass
    try:
        r = urllib.request.urlopen("https://api.exchangerate-api.com/v4/latest/CNY", timeout=8)
        d = json.loads(r.read().decode())
        data["rate"] = f"{d['rates'].get('RUB','?'):.2f}"
    except: pass
    try:
        r = sp.run(["curl","-sL","--connect-timeout","10","https://www.epochtimes.com/gb/nsc413.htm/feed"],
            capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and r.stdout:
            root = ET.fromstring(r.stdout.encode('utf-8'))
            for item in root.findall(".//item")[:8]:
                t = item.find("title")
                if t is not None and t.text:
                    data["news"].append(t.text)
    except: pass
    idx = int(hashlib.md5(time.strftime("%Y-%m-%d").encode()).hexdigest(),16) % len(FORTUNES)
    data["fortune"] = FORTUNES[idx]
    data["sunset"] = calc_sunset(lat, lon)
    data["beijing"] = (datetime.utcnow() + timedelta(hours=8)).strftime("%H:%M")
    CACHE.write_text(json.dumps(data, ensure_ascii=False))
    return data

if __name__ == "__main__":
    d = fetch()
    print(json.dumps(d, ensure_ascii=False, indent=2))
