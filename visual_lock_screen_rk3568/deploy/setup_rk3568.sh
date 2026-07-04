#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_FILE="/etc/systemd/system/vision-lock-rk3568.service"
PYTHON_BIN=${PYTHON_BIN:-/usr/bin/python3}

if [[ $EUID -ne 0 ]]; then
  echo "请以 sudo 运行: sudo $0"
  exit 1
fi

apt-get update
apt-get install -y python3-venv python3-opencv python3-tk i3lock fonts-wqy-zenhei ttf-wqy-zenhei

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "未找到指定 Python: $PYTHON_BIN"
  echo "回退到 /usr/bin/python3"
  PYTHON_BIN=/usr/bin/python3
fi

mkdir -p /opt/visual_lock_screen_rk3568
mkdir -p /etc/visual-lock-screen
cp -r "$ROOT_DIR"/* /opt/visual_lock_screen_rk3568/
cp "$ROOT_DIR/config/example.yaml" /etc/visual-lock-screen/config.yaml

${PYTHON_BIN} -m venv --system-site-packages /opt/visual_lock_screen_rk3568/.venv
/opt/visual_lock_screen_rk3568/.venv/bin/pip install --upgrade pip >/dev/null
/opt/visual_lock_screen_rk3568/.venv/bin/pip install -r /opt/visual_lock_screen_rk3568/requirements.txt

cat > "$SERVICE_FILE" <<'EOF'
[Unit]
Description=Vision Lock Screen on RK3568
After=multi-user.target

[Service]
Type=simple
User=root
Environment=PYTHONUNBUFFERED=1
Environment=DISPLAY=:0
Environment=VISION_LOCK_SIMPLE_RENDER=0
Environment=VISION_LOCK_FORCE_STYLE=text
Environment=VISION_LOCK_FORCE_BACKEND=overlay
Environment=VISION_LOCK_MONITOR=HDMI-A-1
Environment=VISION_LOCK_OVERLAY_STRETCH=0
Environment=VISION_LOCK_OVERLAY_FULLSCREEN=1
Environment=XAUTHORITY=/var/run/lightdm/root/:0
Environment=PYTHONPATH=/opt/visual_lock_screen_rk3568/src
ExecStartPre=/opt/visual_lock_screen_rk3568/deploy/normalize_hdmi_display.sh || true
ExecStart=/opt/visual_lock_screen_rk3568/.venv/bin/python -m vision_lock.main --config /etc/visual-lock-screen/config.yaml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now vision-lock-rk3568
echo "服务已安装并启动：vision-lock-rk3568"
