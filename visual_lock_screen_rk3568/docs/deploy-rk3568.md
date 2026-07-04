# RK3568 部署说明（视觉识别锁屏 v0.3）

## 1) 预检
- 检查摄像头：`v4l2-ctl --list-devices`
- 检查显示：确保有可用会话（X11/Wayland）以及 `i3lock` 可用
- 检查字体：`/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf`

## 2) 安装依赖与服务
```bash
cd /Users/testing/Downloads/人型电脑天使心/visual_lock_screen_rk3568
sudo ./deploy/setup_rk3568.sh
sudo systemctl status vision-lock-rk3568
```
部署脚本会执行：
- 安装 `python3-venv`、OpenCV/Tk/i3lock；
- 安装 Python 依赖到 `/opt/visual_lock_screen_rk3568/.venv`；
- 复制配置到 `/etc/visual-lock-screen/config.yaml`；
- 注册并启动 `vision-lock-rk3568` 服务。

## 3) 配置参数
- 打开 `/etc/visual-lock-screen/config.yaml`
- `lock.mode: auto/overlay/i3lock`
- `detection.trigger_frames`: 建议 3
- `detection.cooldown_seconds`: 建议 120（按 PRD）
- `lock.auto_unlock_seconds`: overlay 模式下建议 20（按 PRD）

## 4) 一键启动/停止
```bash
sudo systemctl start vision-lock-rk3568
sudo systemctl stop vision-lock-rk3568
sudo systemctl status vision-lock-rk3568
```

## 5) 备选：直接本地运行
```bash
cd /Users/testing/Downloads/人型电脑天使心/visual_lock_screen_rk3568
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/example.yaml config/local.yaml
PYTHONPATH=./src python3 -m vision_lock.main --config config/local.yaml
```

## 6) 与本地文档一致性检查
- PRD 条款：中文提示、异常降级、触发行为、显示策略
- 技术方案：摄像头常驻 + 触发阈值 + 冷却机制
