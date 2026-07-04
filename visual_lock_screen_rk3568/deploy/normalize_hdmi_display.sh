#!/usr/bin/env sh
set -eu

export DISPLAY=${DISPLAY:-:0}

if ! command -v xrandr >/dev/null 2>&1; then
  exit 0
fi

xrandr_q=$(mktemp)
if ! xrandr --query >"$xrandr_q" 2>/dev/null; then
  rm -f "$xrandr_q"
  exit 0
fi

HDMI_OUT=""
for out in $(awk '$2=="connected" {print $1}' "$xrandr_q"); do
  if echo "$out" | grep -qi "^HDMI"; then
    HDMI_OUT="${HDMI_OUT:-$out}"
    continue
  fi
  xrandr --output "$out" --off || true
done

if [ -n "$HDMI_OUT" ]; then
  hdmi_mode=$(awk -v out="$HDMI_OUT" '
    $0 ~ "^" out "$" { section=1; next }
    section && /^[[:space:]]*[0-9]+x[0-9]+/ {
      if ($0 ~ /[*+]/) { print $1; exit }
    }
    section && $0 !~ /^[[:space:]]/ { section=0 }
  ' "$xrandr_q")

  fb_size=$hdmi_mode
  if [ -z "$fb_size" ]; then
    fb_size="1920x1080"
  fi

  fb_w=${fb_size%x*}
  fb_h=${fb_size#*x}
  xrandr --output "$HDMI_OUT" --primary --auto || true
  xrandr --output "$HDMI_OUT" --mode "$fb_size" --rate 60 --pos 0x0 --rotate normal || true
  xrandr --fb "${fb_w}x${fb_h}" || true
fi

rm -f "$xrandr_q"
