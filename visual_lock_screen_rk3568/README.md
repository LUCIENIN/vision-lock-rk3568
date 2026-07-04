# 视觉识别锁屏（RK3568）v0.3

基于本地文档：
- `PRD_visual_lock_screen_v0.2.md`
- `Tech_implementation_plan_visual_lock_screen_v1.md`

目标：
- 使用本地摄像头做人形触发检测。
- 检测稳定后触发自定义中文锁屏画面。
- 支持 i3lock 后端（默认）与 overlay 回退。
- 支持 RK3568 本地部署（systemd）。

## 已检索到的成熟方案（GitHub）

1) [airockchip/rknn_model_zoo](https://github.com/airockchip/rknn_model_zoo)
- 官方明确覆盖 RK3568 平台，并提供 YOLOv8 的 RKNN + Linux 推理示例。
- YOLOv8 README 给出了 `--target rk3568` 的推理示例与转换路径。

2) [i3/i3lock](https://github.com/i3/i3lock)
- Linux 经典全屏锁屏实现，支持 `--image` 自定义背景图。
- 支持使用 PAM，适合做锁屏外观层。

3) [Seeed reComputer-RK CV 示例（YOLOv11）](https://wiki.seeedstudio.com/object_detection_with_yolov11_on_recomputer_rk/)
- 给出 Rockchip 板卡部署姿态和 NPU 摄像头推理参数参考。

## 目录结构

- `config/example.yaml`：运行参数。
- `requirements.txt`：Python 依赖。
- `src/vision_lock/`：核心代码。
  - `main.py`：程序入口。
  - `config.py`：配置读取。
  - `detector.py`：人员检测（HOG fallback，便于无 RKNN 环境先跑通）。
  - `engine.py`：去抖 + 冷却编排。
  - `content.py`：文案聚合（含本地降级）。
  - `lockscreen.py`：锁屏图像渲染 + i3lock / overlay 启动。
  - `overlay.py`：无图形栈时的 overlay 回退。
- `deploy/`：RK3568 安装脚本与 service。
- `docs/design-aesthetic.md`：审美规范。
- `docs/deploy-rk3568.md`：部署手册。
- `docs/github-backup.md`：GitHub 备份指南。
- `tests/output/`：渲染测试输出（已 gitignore）。
- `logs/`：运行时日志。

## 快速运行

```bash
cd /Users/testing/Downloads/人型电脑天使心/visual_lock_screen_rk3568
cp config/example.yaml config/local.yaml
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# 渲染测试（不需要摄像头）：
PYTHONPATH=./src python3 -m vision_lock.main --once --config config/local.yaml --seconds 5 --style high_contrast
# 仅生成锁屏图片（无显示弹窗）：
bash test_render.sh high_contrast
# 或一次性生成所有风格：
bash test_render.sh
```

## 本地开发环境说明

- **macOS**: 已添加 macOS 中文字体支持（PingFang / STHeiti），可在 macOS 上完成渲染测试。
- **字体修复**: 修复了 `load_default()` 返回 BytesIO font.path 导致 `truetype()` 失败的问题。
- **部署脚本修复**: setup_rk3568.sh 中的 PYTHONPATH 路径、python3.8 引用已修正。
- **依赖修复**: numpy 版本从 `<1.17` 升级到 `>=1.19.5,<2.0` 以支持 Python 3.9+。

## RK3568 一键部署

```bash
sudo ./deploy/setup_rk3568.sh
sudo systemctl status vision-lock-rk3568
```

## 设计与执行策略（按你的要求）

- 先用成熟方案（RKNN + i3lock）确认技术方向。
- 再落地最小可运行版，优先保障“检索→检测触发→锁屏展示→可恢复”主链路。
- 使用 `docs/design-aesthetic.md` 对字体、色彩、文案、展示时长做统一。

## GitHub 备份结果

- 目标仓库：`https://github.com/LUCIENIN/visual-lock-screen-rk3568`
- 分支：`main`
- 当前提交可在仓库中追溯（最新为：`chore: add pycache ignore and clean committed artifacts`）

执行备份：
```bash
cd /Users/testing/Downloads/人型电脑天使心/visual_lock_screen_rk3568
./deploy/github-backup.sh
```
