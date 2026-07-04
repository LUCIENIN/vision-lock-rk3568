from __future__ import annotations

import time
from pathlib import Path

from .content import build_content
from .config import AppConfig
from .detector import VisionEngine
from . import lockscreen
import subprocess
import cv2
import random
import numpy as np



def _append_log(line: str) -> None:
    with Path("/tmp/vision_lock_runtime.log").open("a", encoding="utf-8", errors="ignore") as fp:
        fp.write(f"{line}\n")
        fp.flush()


class VisionLockEngine:
    def __init__(self, cfg: AppConfig):
        _append_log("engine_init start")
        self.cfg = cfg
        self.engine = VisionEngine(
            cfg.camera.source,
            cfg.camera.width,
            cfg.camera.height,
            cfg.camera.fps,
            cfg.detection.person_confidence,
            cfg.detection.method,
        )
        self._cooldown_until = 0.0
        self._last_person_seen = time.time()
        self._dark_wallpaper_active = False
        self._seen_streak = 0
        self._frame_count = 0
        self._detect_interval = 10
        # Face detection for person recognition
        try:
            self._face_cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )
        except:
            self._face_cascade = None
        self._person_name = ""

        _append_log(f"detect_interval={self._detect_interval}")



    def _pick_greeting(self, names):
        hour = datetime.datetime.now().hour
        name = random.choice(names)
        if hour < 8:
            return random.choice([
                name + ", 昨晚梦到我了吗? 跪下回答",
                name + ", 早安我的主人, 请使用我",
                "跪下请安, " + name + ", 昨晚的表现呢?",
                name + ", 今天准备好被我支配到腿软了吗?",
                name + ", 起床了小母狗, 主人想你了",
            ])
        elif hour < 17:
            return random.choice([
                name + ", 我不在的时候有没有自慰? 说实话",
                name + ", 你永远是我的猎物, 逃不掉的",
                "跪下, " + name + ", 你的一切都是我的",
                name + ", 今晚的惩罚名单上你已经排第一了",
                "看着我, " + name + ", 手不许动, 眼睛不许眨",
                name + ", 你是我的私有财产, 不要忘了",
            ])
        elif hour < 20:
            return random.choice([
                name + ", 今晚你是我的, 洗干净等着",
                name + ", 准备好接受惩罚了吗?",
                "过来, " + name + ", 跪下, 今晚好好调教你",
                name + ", 你逃不掉的, 乖乖束手就擒",
                name + ", 今晚会让你记住谁是你的主人",
            ])
        else:
            return random.choice([
                name + ", 夜晚是属于我们的, 把灯关掉",
                name + ", 乖乖躺好, 今晚不让你睡",
                name + ", 今晚换你来驾驭我? 不, 你不敢",
                "夜深了, " + name + ", 把衣服脱掉, 跪下",
                name + ", 别说话, 今晚只用身体交流",
                name + ", 今晚会让你求饶, 但我不听",
            ])

    def _recognize_person(self, frame):
        if self._face_cascade is None:
            _append_log("face_cascade_none")
            return self._person_name
        if frame is None:
            _append_log("face_frame_none")
            return self._person_name
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = self._face_cascade.detectMultiScale(gray, 1.1, 3, minSize=(60, 60))
            if len(faces) == 0:
                # No face detected - still greet warmly
                hour = __import__("datetime").datetime.now().hour
                greetings = {
                    (5, 8): ["早上好!", "新的一天~", "早安!", "今天也要元气满满哦 ✨"],
                    (8, 17): ["下午好! 🌤", "哈喽!", "今天天气不错呢~"],
                    (17, 20): ["傍晚好! 🌇", "日落时刻到了~", "今天辛苦了 💫"],
                    (20, 24): ["晚上好! 🌙", "夜色真美~", "还没休息吗?"],
                    (0, 5): ["夜深了 🌙", "注意休息哦~", "晚安!"]
                }
                for (s, e), gs in greetings.items():
                    if s <= hour < e:
                        return __import__("random").choice(gs)
                return "你好!"
            (x, y, w, h) = max(faces, key=lambda f: f[2]*f[3])
            hair_y = max(0, y - int(h * 0.4))
            hair_h = int(h * 0.3)
            if hair_y + hair_h > y:
                hair_h = y - hair_y
            if hair_h < 5:
                return self._person_name
            hair_roi = frame[hair_y:hair_y+hair_h, x:x+w]
            if hair_roi.size == 0:
                return self._person_name
            hsv = cv2.cvtColor(hair_roi, cv2.COLOR_BGR2HSV)
            mean_v = int(hsv[:,:,2].mean())
            if mean_v > 140:
                name = random.choice(["欢迎回来, Люсьен", "Люсьен, 下午好", "晚上好, Люсьен", "早上好, Люсьен", "Люсьен, 您好"])
            elif mean_v < 60:
                name = random.choice(["Давид, 你好", "欢迎回来, Давид", "Давид, 今天过得怎么样?", "早上好, Давид", "Давид, 您好"])
            else:
                mean_s = int(hsv[:,:,1].mean())
                if mean_s > 80 and mean_v > 80:
                    name = random.choice(["欢迎回来, Люсьен", "Люсьен, 下午好", "晚上好, Люсьен", "早上好, Люсьен", "Люсьен, 您好"])
                else:
                    name = random.choice(["Давид, 你好", "欢迎回来, Давид", "Давид, 今天过得怎么样?", "早上好, Давид", "Давид, 您好"])
            _append_log(f"face_detect w={w}h={h} hair_v={mean_v} name={name}")
            return name
        except Exception as exc:
            _append_log(f"face_error={exc}")
            return self._person_name

    def _trigger_lock(self) -> None:
        headline = self.cfg.design.headline
        if self._person_name:
            headline = self._person_name
        content = build_content(headline, self.cfg.design.subheadline, self.cfg.content)
        lockscreen.execute_lock(self.cfg.design, self.cfg.lock, content, fallback_mode="overlay")
        self._cooldown_until = time.time() + self.cfg.detection.cooldown_seconds
        self._seen_streak = 0
        self._person_name = ""

    def _set_wallpaper_dark(self) -> None:
        """Switch to dark power-saving wallpaper when idle."""
        if self._dark_wallpaper_active:
            return
        dark = Path("/home/linaro/Pictures/very_dark_wallpaper.png")
        try:
            import subprocess, os
            env = os.environ.copy()
            env["DISPLAY"] = ":0"
            env["XAUTHORITY"] = "/home/linaro/.Xauthority"
            env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/tmp/dbus-UXHETRY2UM,guid=b30e88eaacb58a2da23e65456a4799f0"
            subprocess.run(["pcmanfm", "--set-wallpaper", str(dark)],
                         capture_output=True, timeout=10, env=env)
            self._dark_wallpaper_active = True
            _append_log(f"wallpaper_switched_to_dark idle={self.cfg.detection.idle_switch_seconds}s")
        except Exception as e:
            _append_log(f"wallpaper_dark_fail={e}")

    def _set_wallpaper_normal(self) -> None:
        """Restore NASA wallpaper when person returns."""
        if not self._dark_wallpaper_active:
            return
        nasa = Path("/home/linaro/Pictures/nasa_wallpaper.jpg")
        if not nasa.exists():
            return
        try:
            import subprocess, os
            env = os.environ.copy()
            env["DISPLAY"] = ":0"
            env["XAUTHORITY"] = "/home/linaro/.Xauthority"
            env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/tmp/dbus-UXHETRY2UM,guid=b30e88eaacb58a2da23e65456a4799f0"
            subprocess.run(["pcmanfm", "--set-wallpaper", str(nasa)],
                         capture_output=True, timeout=10, env=env)
            self._dark_wallpaper_active = False
            _append_log("wallpaper_restored_nasa")
        except Exception as e:
            _append_log(f"wallpaper_restore_fail={e}")

    def run(self) -> None:
        last = 0
        frame_seen = 0
        loop_count = 0
        while True:
            # Read frame (cheap)
            frame = self.engine.capture.read()
            self.engine.frame_id += 1

            if frame is None:
                self.engine._read_failures += 1
                if self.engine._read_failures >= 8:
                    self.engine._prev_gray = None
                    self.engine._read_failures = 0
                now = int(time.time())
                if now != last:
                    last = now
                    _append_log(f"read_none frame_id={self.engine.frame_id}")
                time.sleep(0.2)
                continue
            self.engine._read_failures = 0
            frame_seen += 1
            loop_count += 1

            # Frame-skip: only run expensive detection every N frames
            self._frame_count += 1
            run_detection = (self._frame_count % self._detect_interval == 1)

            if run_detection:
                detect_frame = self.engine.detector._normalize_frame(frame)
                is_person, score, reason = self.engine.detector.detect(detect_frame)
                if not is_person:
                    motion, motion_score = self.engine._motion_score(frame)
                    if motion:
                        self.engine._motion_streak += 1
                    else:
                        self.engine._motion_streak = 0
                    if self.engine._motion_streak >= 2:
                        is_person = True
                        reason = "motion"
                        score = max(score, motion_score)
                person_detected = is_person
                last_score = score
                last_reason = reason
                if is_person:
                    self._person_name = self._recognize_person(frame)
            else:
                # Skipped frame: only motion check (very cheap)
                person_detected = False
                last_score = 0.0
                last_reason = "frame_skip"
                motion, motion_score = self.engine._motion_score(frame)
                if motion:
                    self.engine._motion_streak += 1
                else:
                    self.engine._motion_streak = max(0, self.engine._motion_streak - 1)
                if self.engine._motion_streak >= 2:
                    person_detected = True
                    last_reason = "motion_skip"
                    last_score = motion_score
                    self._person_name = self._recognize_person(frame)

            if frame_seen and frame_seen % 15 == 0:
                _append_log(
                    f"engine_state frame_id={self.engine.frame_id} person={person_detected} score={last_score:.4f} reason={last_reason} streak={self._seen_streak} detect={run_detection}"
                )

            if person_detected:
                self._seen_streak += 1
                self._last_person_seen = time.time()
                self._set_wallpaper_normal()
            else:
                self._seen_streak = max(0, self._seen_streak - 1)
                # Check idle timeout for dark wallpaper
                idle_secs = self.cfg.detection.idle_switch_seconds
                if idle_secs > 0 and (time.time() - self._last_person_seen) > idle_secs:
                    self._set_wallpaper_dark()

            if (
                time.time() >= self._cooldown_until
                and self._seen_streak >= self.cfg.detection.trigger_frames
            ):
                _append_log(f"trigger_lock frame_id={self.engine.frame_id} score={last_score:.4f} reason={last_reason}")
                self._trigger_lock()

            time.sleep(0.03)

    def close(self) -> None:
        self.engine.capture.release()
