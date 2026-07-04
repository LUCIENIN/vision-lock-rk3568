#!/usr/bin/env bash
# 视觉识别锁屏 - 渲染测试脚本
# 用法: ./test_render.sh [style]
# 不传 style 参数则渲染所有风格

set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv/bin/python"
SRC="src"
CONFIG="config/local.yaml"

if [[ ! -f "$VENV" ]]; then
  echo "❌ 未找到虚拟环境，请先运行: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

STYLE="${1:-}"
OUTDIR="tests/output"

if [[ -n "$STYLE" ]]; then
  echo "🎨 渲染风格: $STYLE"
  PYTHONPATH="$SRC" "$VENV" -c "
from pathlib import Path
from vision_lock.config import load_config
from vision_lock.content import build_content
from vision_lock.lockscreen import render_lock_screen

cfg = load_config('$CONFIG')
cfg.design.style = '$STYLE'
content = build_content(cfg.design.headline, cfg.design.subheadline, cfg.content)
out = Path('$OUTDIR/test_lock_screen_${STYLE}.png')
result = render_lock_screen(cfg.design, content, out, auto_close_seconds=12)
kb = result.image_path.stat().st_size / 1024
print(f'✅ {result.image_path} ({kb:.0f} KB)')
"
else
  for style in classic github_wallpaper fancy cyberpunk cyber plain safe text high_contrast; do
    echo "  🎨 $style..."
    PYTHONPATH="$SRC" "$VENV" -c "
from pathlib import Path
from vision_lock.config import load_config
from vision_lock.content import build_content
from vision_lock.lockscreen import render_lock_screen

cfg = load_config('$CONFIG')
cfg.design.style = '$style'
content = build_content(cfg.design.headline, cfg.design.subheadline, cfg.content)
out = Path('$OUTDIR/test_lock_screen_${style}.png')
result = render_lock_screen(cfg.design, content, out, auto_close_seconds=12)
kb = result.image_path.stat().st_size / 1024
print(f'✅ {style}: {kb:.0f} KB')
"
  done
fi
