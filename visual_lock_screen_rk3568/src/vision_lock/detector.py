from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import importlib
import os
import shutil
import subprocess
import time

import cv2
import numpy as np


def _append_detector_log(line: str) -> None:
    with Path("/tmp/vision_lock_detector.log").open("a", encoding="utf-8", errors="ignore") as fp:
        fp.write(f"{line}\n")
        fp.flush()


def _safe_supervision_import() -> tuple[object | None, str]:
    try:
        module = importlib.import_module("supervision")
        return module, "ok"
    except Exception as exc:
        return None, f"{type(exc).__name__}:{exc}"


def _build_supervision_detections(module: object | None, boxes: list[tuple[int, int, int, int]], scores: list[float]):
    if module is None or not boxes or not scores:
        return None
    cls = getattr(module, "Detections", None)
    if cls is None:
        return None
    try:
        return cls(
            xyxy=np.asarray(boxes, dtype=np.float32),
            confidence=np.asarray(scores, dtype=np.float32),
            class_id=np.zeros(len(boxes), dtype=np.int32),
        )
    except Exception:
        return None


def _append_log(path: Path, line: str) -> None:
    try:
        with path.open("a", encoding="utf-8", errors="ignore") as fp:
            fp.write(f"{line}\n")
            fp.flush()
    except Exception:
        pass


def _force_mjpg_format(device: str, width: int, height: int) -> bool:
    probe_log = Path("/tmp/vision_lock_camera_probe.log")
    if not device.startswith("/dev/video"):
        return False
    v4l2_ctl = shutil.which("v4l2-ctl")
    if v4l2_ctl is None:
        _append_log(probe_log, f"force_mjpg_skip no_v4l2_ctl device={device}")
        return False
    fmt = f"width={width},height={height},pixelformat=MJPG"
    try:
        proc = subprocess.run(
            [v4l2_ctl, "-d", device, "--set-fmt-video", fmt],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=1.5,
        )
    except Exception as exc:
        _append_log(probe_log, f"force_mjpg_error device={device} err={type(exc).__name__}:{exc}")
        return False

    if proc.returncode == 0:
        _append_log(probe_log, f"force_mjpg_ok device={device} fmt={fmt}")
        return True

    _append_log(
        probe_log,
        f"force_mjpg_fail device={device} rc={proc.returncode} err={(proc.stderr or '').strip()[:180]}",
    )
    return False


def _candidate_devices(source) -> list[str]:
    requested = str(source)
    if requested.isdigit():
        requested = f"/dev/video{requested}"

    devices: list[str] = []
    if requested:
        devices.append(requested)
    for path in ("/dev/video9", "/dev/video10", "/dev/video-camera0"):
        if path not in devices:
            devices.append(path)
    for path in sorted(Path("/dev").glob("video*")):
        name = path.name
        if name == "video-camera0" or (name.startswith("video") and name[5:].isdigit()):
            p = f"/dev/{name}"
            if p not in devices:
                devices.append(p)

    def rank(item: str) -> tuple[int, str]:
        if item == requested:
            return (0, item)
        if item in {"/dev/video9", "/dev/video10"}:
            return (1, item)
        return (2, item)

    seen = dict.fromkeys(device for device in devices if device.startswith("/dev/video"))
    return sorted(seen.keys(), key=rank)


def _capture_try_list(width: int, height: int, fps: int) -> list[tuple[int, int, int, int | None]]:
    mjpg = cv2.VideoWriter_fourcc(*"MJPG")
    yuyv = cv2.VideoWriter_fourcc(*"YUYV")
    return [
        (width, height, max(1, int(fps)), mjpg),
        (width, height, 10, mjpg),
        (1280, 720, 10, mjpg),
        (1920, 1080, 10, mjpg),
        (640, 480, 10, mjpg),
        (640, 480, 10, yuyv),
        (width, height, max(1, int(fps)), None),
    ]


_MOTION_SCORE_THRESHOLD = 0.003
_MOTION_STREAK = 2


@dataclass
class VisionState:
    frame_id: int
    raw_frame: np.ndarray | None
    is_person: bool
    confidence: float
    reason: str = "none"


class HOGPersonDetector:
    """CPU fallback detector. RKNN 优先链路可在配置中自行替换为独立脚本调用。"""

    def __init__(self, conf_threshold: float = 0.5, method: str = "auto"):
        self._threshold = conf_threshold
        self._method = (method or "auto").lower().strip()
        if self._method not in {"auto", "hog", "cascade"}:
            self._method = "auto"
        self._hog: cv2.HOGDescriptor | None = None
        try:
            self._hog = cv2.HOGDescriptor()
            self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
            _append_detector_log("hog_init ok")
        except Exception:
            self._hog = None
            _append_detector_log("hog_init fail")
        self._hog_daimler = None
        self._fullbody = self._load_cascade("haarcascade_fullbody.xml")
        self._upperbody = self._load_cascade("haarcascade_upperbody.xml")
        self._front_face = self._load_cascade("haarcascade_frontalface_default.xml")
        _append_detector_log(
            f"cascade_ready fullbody={'yes' if self._fullbody is not None else 'no'} "
            f"upperbody={'yes' if self._upperbody is not None else 'no'} "
            f"face={'yes' if self._front_face is not None else 'no'}"
        )

    @staticmethod
    def _load_cascade(filename: str) -> cv2.CascadeClassifier | None:
        candidates: list[str] = []
        path = None
        if hasattr(cv2, "data") and getattr(cv2, "data", None) is not None and hasattr(cv2.data, "haarcascades"):
            candidates.append(os.path.join(cv2.data.haarcascades, filename))

        candidates.extend(
            [
                os.path.join("/usr/share/opencv4/haarcascades", filename),
                os.path.join("/usr/share/opencv/haarcascades", filename),
                os.path.join("/usr/local/share/opencv4/haarcascades", filename),
                "/opt/visual_lock_screen_rk3568/assets/haarcascades/" + filename,
                "/opt/visual_lock_screen_rk3568/visual_lock_screen_rk3568/assets/haarcascades/" + filename,
            ]
        )

        project_root = Path(__file__).resolve().parents[2]
        candidates.append(str(project_root / "assets" / "haarcascades" / filename))

        for candidate in candidates:
            if os.path.exists(candidate):
                path = candidate
                break

        if path is None:
            return None
        cascade = cv2.CascadeClassifier(path)
        return cascade if not cascade.empty() else None

    def _normalize_frame(self, frame: np.ndarray) -> np.ndarray:
        height, width = frame.shape[:2]
        if height >= width:
            # 某些 USB 摄像头会出竖向帧，先改到横向再做人形检测
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)

        # 限制检测计算尺寸，防止在 RK3568 上 HOG 堵塞。
        max_width = 720
        if frame.shape[1] > max_width:
            scale = max_width / float(frame.shape[1])
            new_w = max(1, int(round(frame.shape[1] * scale)))
            new_h = max(1, int(round(frame.shape[0] * scale)))
            frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
        return frame

    def _hog_detect(self, frame: np.ndarray) -> tuple[bool, float, str]:
        if self._hog is None:
            return False, 0.0, "hog_disabled"

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        try:
            (rects, weights) = self._hog.detectMultiScale(
                gray,
                winStride=(8, 8),
                padding=(16, 16),
                scale=1.05,
                hitThreshold=max(float(self._threshold) - 0.07, -0.35),
                useMeanshiftGrouping=False,
            )
        except Exception:
            # 某些 OpenCV 发行版的 HOG 在 RK 平台偶发不兼容，交给级联继续判断。
            rects = ()
            weights = []
        if len(rects) == 0:
            # 不使用 Daimler 模型路径，避免旧版 OpenCV 在该模型下的 setSVMDetector 兼容性问题。
            return False, 0.0, "hog_none"
        score = float(max(weights)) if len(weights) else 0.0
        if score < max(float(self._threshold), 0.02):
            return False, 0.0, "hog_low_score"
        return True, score, "hog"

    def _cascade_detect(self, frame: np.ndarray) -> tuple[bool, float, str]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        h, w = gray.shape
        area = float(max(1, h * w))
        if area <= 0:
            return False, 0.0, "cascade_none"
        best_score = 0.0
        found = False
        best_rect = (0, 0, 0, 0)

        min_face_ratio = 0.00012
        min_body_ratio = 0.0009
        max_body_ratio = 0.80

        def _area_ratio(rect) -> float:
            _, _, rw, rh = rect
            if rw <= 0 or rh <= 0:
                return 0.0
            return (rw * rh) / area

        def _aspect_ok(rect) -> bool:
            _, _, rw, rh = rect
            if rw <= 0 or rh <= 0:
                return False
            ratio = rw / float(rh)
            return 0.2 <= ratio <= 1.8

        if self._fullbody is not None:
            rects = self._fullbody.detectMultiScale(
                gray,
                scaleFactor=1.06,
                minNeighbors=1,
                minSize=(24, 40),
            )
            if len(rects) > 0:
                found = True
                for _, _, rw, rh in rects:
                    ratio = _area_ratio((0, 0, rw, rh))
                    if ratio < min_body_ratio or ratio > max_body_ratio or not _aspect_ok((0, 0, rw, rh)):
                        continue
                    if rw * rh > best_rect[2] * best_rect[3]:
                        best_rect = (0, 0, rw, rh)
                _, _, rw, rh = best_rect
                score_ratio = _area_ratio(best_rect)
                if score_ratio >= min_body_ratio:
                    best_score = max(best_score, min(1.0, score_ratio * 2.6))

        if self._upperbody is not None:
            best_rect = (0, 0, 0, 0)
            rects = self._upperbody.detectMultiScale(
                gray,
                scaleFactor=1.06,
                minNeighbors=1,
                minSize=(20, 32),
            )
            if len(rects) > 0:
                found = True
                for _, _, rw, rh in rects:
                    ratio = _area_ratio((0, 0, rw, rh))
                    if ratio < min_body_ratio or ratio > max_body_ratio or not _aspect_ok((0, 0, rw, rh)):
                        continue
                    if rw * rh > best_rect[2] * best_rect[3]:
                        best_rect = (0, 0, rw, rh)
                _, _, rw, rh = best_rect
                score_ratio = _area_ratio(best_rect)
                if score_ratio >= min_body_ratio:
                    best_score = max(best_score, min(1.0, score_ratio * 4.0))

        if self._front_face is not None:
            best_rect = (0, 0, 0, 0)
            rects = self._front_face.detectMultiScale(
                gray,
                scaleFactor=1.05,
                minNeighbors=1,
                minSize=(24, 24),
            )
            if len(rects) > 0:
                # 仅当人脸占比明显时才作为有效人体信号，降低误报
                found = True
                for _, _, rw, rh in rects:
                    ratio = _area_ratio((0, 0, rw, rh))
                    if ratio < min_face_ratio:
                        continue
                    if rw * rh > best_rect[2] * best_rect[3]:
                        best_rect = (0, 0, rw, rh)
                _, _, rw, rh = best_rect
                ratio = (rw * rh) / area
                if ratio > min_face_ratio:
                    best_score = max(best_score, min(1.0, ratio * 50.0))

        return found, best_score, "cascade" if found else "cascade_none"

    def detect(self, frame: np.ndarray) -> tuple[bool, float, str]:
        candidates: list[np.ndarray] = [frame]
        if frame.shape[0] != frame.shape[1]:
            try:
                candidates.append(cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE))
            except Exception:
                pass

        best_score = 0.0
        best_reason = "hog_none"
        allow_hog = self._method in {"auto", "hog"}
        allow_cascade = self._method in {"auto", "cascade"}
        for candidate in candidates:
            if allow_hog:
                is_person, score, reason = self._hog_detect(candidate)
                if is_person:
                    return True, score, reason
                if score > best_score:
                    best_score = score
                    best_reason = reason

        for candidate in candidates:
            if allow_cascade:
                cascade_is_person, cascade_score, cascade_reason = self._cascade_detect(candidate)
                if cascade_is_person and cascade_score >= max(0.02, float(self._threshold) * 0.7):
                    return True, max(best_score, cascade_score), cascade_reason
                if cascade_is_person and cascade_score > best_score:
                    best_score = cascade_score
                    best_reason = cascade_reason

        return False, best_score, best_reason


class SupervisionPersonDetector:
    """Use supervision detection container when optional dependency is available."""

    def __init__(self, conf_threshold: float = 0.5, method: str = "auto"):
        self._fallback = HOGPersonDetector(conf_threshold, "auto")
        self._module, status = _safe_supervision_import()
        _append_detector_log(f"supervision_import={status}")

    def _normalize_frame(self, frame: np.ndarray) -> np.ndarray:
        return self._fallback._normalize_frame(frame)

    def _derive_supervision_detections(self, frame: np.ndarray, score: float) -> object | None:
        if self._module is None:
            return None
        h, w = frame.shape[:2]
        boxes = [(0, 0, w - 1, h - 1)]
        scores = [float(score)]
        return _build_supervision_detections(self._module, boxes, scores)

    def detect(self, frame: np.ndarray) -> tuple[bool, float, str]:
        is_person, score, reason = self._fallback.detect(frame)
        if not is_person:
            return False, score, reason

        detections = self._derive_supervision_detections(frame, score)
        if detections is None:
            return True, score, reason

        try:
            count = len(detections)
        except Exception:
            count = 1
        if count <= 0:
            return False, 0.0, "supervision_empty"
        return True, score, "supervision"


class VisionCapture:
    def __init__(self, source, width: int, height: int, fps: int):
        devices = _candidate_devices(source)
        candidates = _capture_try_list(width, height, fps)
        self.cap: cv2.VideoCapture | None = None
        self.source: str | None = None
        log = Path("/tmp/vision_lock_camera_probe.log")
        self._counter = 0

        for device in devices:
            for target_w, target_h, target_fps, fourcc in candidates:
                try:
                    _force_mjpg_format(device, target_w, target_h)
                    cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
                except Exception as exc:
                    _append_log(log, f"camera_init_error device={device} err={type(exc).__name__}:{exc}")
                    continue

                if not cap.isOpened():
                    cap.release()
                    _append_log(log, f"camera_open_false device={device} size={target_w}x{target_h} fps={target_fps} fourcc={fourcc}")
                    continue

                if target_w is not None:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, target_w)
                if target_h is not None:
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, target_h)
                if target_fps is not None:
                    cap.set(cv2.CAP_PROP_FPS, target_fps)
                if fourcc is not None:
                    cap.set(cv2.CAP_PROP_FOURCC, fourcc)
                cap.set(cv2.CAP_PROP_CONVERT_RGB, 1)

                frame = None
                for _ in range(5):
                    time.sleep(0.05)
                    ok, frame = cap.read()
                    if ok and frame is not None:
                        break

                if ok and frame is not None:
                    self.cap = cap
                    self.source = device
                    _append_log(
                        log,
                        f"camera_open_ok device={device} size={target_w}x{target_h} fps={target_fps} fourcc={fourcc} frame={frame.shape}",
                    )
                    return

                cap.release()
                _append_log(log, f"camera_read_fail device={device} size={target_w}x{target_h} fps={target_fps} fourcc={fourcc}")

        _append_log(log, f"camera_all_failed source={source} devices={devices}")
        raise RuntimeError(f"摄像头打开失败: {source}")

    def read(self) -> np.ndarray | None:
        if self.cap is None:
            return None

        ok, frame = self.cap.read()
        if not ok or frame is None:
            return None
        self._counter += 1
        return frame

    def release(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None


class VisionEngine:
    def __init__(self, source: str | int, width: int, height: int, fps: int, conf_threshold: float, method: str = "auto"):
        self.capture = VisionCapture(source, width, height, fps)
        selected_method = (method or "auto").lower().strip()
        if selected_method == "supervision":
            self.detector = SupervisionPersonDetector(conf_threshold, selected_method)
        else:
            self.detector = HOGPersonDetector(conf_threshold, selected_method)
        _append_detector_log(f"detector_selected={type(self.detector).__name__}")
        self.frame_id = 0
        self._prev_gray: np.ndarray | None = None
        self._motion_streak = 0
        self._read_failures = 0

    @staticmethod
    def _safe_gray(frame: np.ndarray) -> np.ndarray:
        if frame.ndim == 3:
            return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return frame

    def _motion_score(self, frame: np.ndarray) -> tuple[bool, float]:
        gray = self._safe_gray(frame)
        h, w = gray.shape[:2]
        area = max(1, h * w)

        if self._prev_gray is None:
            self._prev_gray = gray
            return False, 0.0

        prev = self._prev_gray
        self._prev_gray = gray

        diff = cv2.absdiff(prev, gray)
        _, fg = cv2.threshold(diff, 20, 255, cv2.THRESH_BINARY)
        fg = cv2.medianBlur(fg, 5)
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, k)
        moved = cv2.countNonZero(fg)
        ratio = float(moved) / float(area)
        return ratio > _MOTION_SCORE_THRESHOLD, min(1.0, ratio * 140.0)

    def infer(self) -> VisionState:
        frame = self.capture.read()
        self.frame_id += 1
        if frame is None:
            self._read_failures += 1
            if self._read_failures >= 8:
                self._prev_gray = None
                self._read_failures = 0
            return VisionState(self.frame_id, None, False, 0.0)
        self._read_failures = 0
        # 降采样到可控分辨率再做人形检测，避免长时间阻塞。
        detect_frame = self.detector._normalize_frame(frame)
        is_person, score, reason = self.detector.detect(detect_frame)
        if not is_person:
            motion, motion_score = self._motion_score(frame)
            if motion:
                self._motion_streak += 1
            else:
                self._motion_streak = 0

            if self._motion_streak >= _MOTION_STREAK:
                is_person = True
                reason = "motion"
                score = max(score, motion_score)
            else:
                score = max(score, motion_score)
                reason = "motion_streak0"

        return VisionState(self.frame_id, frame, is_person, score, reason=reason if is_person else ("none" if score == 0.0 else reason))

    def close(self) -> None:
        self.capture.release()
