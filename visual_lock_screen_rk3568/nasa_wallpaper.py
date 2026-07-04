#!/opt/visual_lock_screen_rk3568/.venv/bin/python3
"""Download NASA image of the day and set as desktop wallpaper for LXDE."""
import urllib.request, xml.etree.ElementTree as ET, os, subprocess as sp
from pathlib import Path

WALLPAPER_DIR = Path("/home/linaro/Pictures")
WALLPAPER_FILE = WALLPAPER_DIR / "nasa_wallpaper.jpg"

def fetch_nasa_image():
    """Get the latest NASA image of the day URL."""
    try:
        r = urllib.request.urlopen("https://www.nasa.gov/rss/dyn/lg_image_of_the_day.rss", timeout=15)
        root = ET.fromstring(r.read())
        items = root.findall(".//item")
        if not items:
            return None
        for item in items:
            enclosure = item.find("enclosure")
            if enclosure is not None:
                url = enclosure.get("url", "")
                if url and url.endswith((".jpg", ".jpeg", ".png")):
                    return url
        return None
    except Exception as e:
        print(f"Error fetching NASA feed: {e}")
        return None

def download_image(url):
    try:
        r = urllib.request.urlopen(url, timeout=30)
        data = r.read()
        WALLPAPER_DIR.mkdir(parents=True, exist_ok=True)
        (WALLPAPER_DIR / "nasa_original.jpg").write_bytes(data)
        print(f"Downloaded: {url} ({len(data)} bytes)")
        return True
    except Exception as e:
        print(f"Error downloading: {e}")
        return False

def resize_image():
    """Resize to fit 1920x1080, keep aspect ratio."""
    try:
        from PIL import Image
        img = Image.open(WALLPAPER_DIR / "nasa_original.jpg")
        img.thumbnail((1920, 1080), Image.LANCZOS)
        img.save(WALLPAPER_FILE, quality=85, optimize=True)
        print(f"Resized: {img.size}")
        os.chown(str(WALLPAPER_FILE), 1000, 1000)  # linaro:linaro
        return True
    except Exception as e:
        print(f"Error resizing: {e}")
        return False

def set_wallpaper():
    """Set wallpaper via pcmanfm (LXDE) using dbus."""
    if not WALLPAPER_FILE.exists():
        return False
    try:
        # Update config files for all monitors
        for i in range(3):
            conf = Path(f"/home/linaro/.config/pcmanfm/LXDE/desktop-items-{i}.conf")
            conf.parent.mkdir(parents=True, exist_ok=True)
            conf.write_text(f"""[*]
wallpaper_mode=crop
wallpaper_common=1
wallpaper={WALLPAPER_FILE}
desktop_bg=#000000
desktop_fg=#ffffff
desktop_shadow=#000000
desktop_font=Sans 12
show_wm_menu=0
sort=mtime;ascending;
show_documents=0
show_trash=1
show_mounts=0
""")
        os.chown(str(conf.parent), 1000, 1000)
        
        # Use pcmanfm via dbus to apply
        env = os.environ.copy()
        env["DISPLAY"] = ":0"
        env["XAUTHORITY"] = "/home/linaro/.Xauthority"
        env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/tmp/dbus-UXHETRY2UM,guid=b30e88eaacb58a2da23e65456a4799f0"
        result = sp.run(["pcmanfm", "--set-wallpaper", str(WALLPAPER_FILE)],
                       capture_output=True, text=True, timeout=10, env=env)
        if result.returncode == 0:
            print("Wallpaper set via pcmanfm")
            return True
        
        # Fallback: SIGHUP pcmanfm
        pid = sp.run(["pgrep", "-u", "linaro", "-x", "pcmanfm"], capture_output=True, text=True, timeout=5)
        if pid.returncode == 0 and pid.stdout.strip():
            os.kill(int(pid.stdout.strip()), sp.signal.SIGHUP)
            print("Wallpaper set via SIGHUP")
            return True
    except Exception as e:
        print(f"Error setting wallpaper: {e}")
    return False

if __name__ == "__main__":
    url = fetch_nasa_image()
    if url:
        if download_image(url):
            if resize_image():
                set_wallpaper()
    else:
        print("No image found")
