#!/usr/bin/env bash
set -euo pipefail

set -x

echo "[1/4] 服务状态"
systemctl is-enabled vision-lock-rk3568
systemctl is-active vision-lock-rk3568

echo "[2/4] 配置确认"
cat /etc/visual-lock-screen/config.yaml

echo "[3/4] 本地采集可用性自检（会临时停止服务）"
systemctl stop vision-lock-rk3568
if [[ -x /opt/visual_lock_screen_rk3568/.venv/bin/python ]]; then
  /opt/visual_lock_screen_rk3568/.venv/bin/python - <<'PY'
import cv2

source = '/dev/video9'
cap = cv2.VideoCapture(source, cv2.CAP_V4L2)
if not cap.isOpened():
    raise SystemExit("FAILED: 视频设备未打开")
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
cap.set(cv2.CAP_PROP_FPS, 10)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
ok, frame = cap.read()
if not ok or frame is None:
    raise SystemExit("FAILED: 未读取到帧")
print("OK: read frame shape =", tuple(frame.shape))
cap.release()
PY
else
  echo "未找到 /opt/visual_lock_screen_rk3568/.venv/bin/python，回退到系统 python3"
  python3 - <<'PY'
import cv2

source = '/dev/video9'
cap = cv2.VideoCapture(source, cv2.CAP_V4L2)
if not cap.isOpened():
    raise SystemExit("FAILED: 视频设备未打开")
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
cap.set(cv2.CAP_PROP_FPS, 10)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
ok, frame = cap.read()
if not ok or frame is None:
    raise SystemExit("FAILED: 未读取到帧")
print("OK: read frame shape =", tuple(frame.shape))
cap.release()
PY
fi

systemctl start vision-lock-rk3568

echo "[4/4] 服务稳定性"
sleep 20
systemctl is-active vision-lock-rk3568
systemctl show -p NRestarts,MainPID --value vision-lock-rk3568
echo "verify done"
