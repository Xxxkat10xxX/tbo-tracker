#!/usr/bin/env bash
# Демо-ролик tbo-tracker (≈1:31, 1920×1080): чистые демо-данные → запись сценария Playwright → MP4.
# Запуск из корня репозитория: bash promo/demo/make-video.sh   (нужны Python-зависимости, Node + Playwright, ffmpeg)
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/../.." && pwd)"
DATA="$(mktemp -d)"; PORT=8766
cd "$ROOT" && TBO_DEMO_MODE=1 TBO_DATA_DIR="$DATA" TBO_PORT=$PORT python -m app.serve > "$DATA/server.log" 2>&1 &
SRV=$!; trap 'kill $SRV 2>/dev/null; rm -rf "$DATA"' EXIT
for i in $(seq 1 30); do [ -f "$DATA/setup-code.txt" ] && break; sleep 1; done; sleep 2
cd "$HERE" && TBO_DEMO_URL="http://127.0.0.1:$PORT" node record.js "$DATA/setup-code.txt"
python3 - <<'PY'
import json
st=json.load(open('stamps.json')); end=dict(json.load(open('timeline.json')))['stop']
out=[]
for k,(i,ms) in enumerate(st):
    nxt=st[k+1][1] if k+1<len(st) else end*1000
    out.append(f"file 'frames/f{i:06d}.jpg'\nduration {max(0.001,(nxt-ms)/1000):.4f}")
out.append(f"file 'frames/f{st[-1][0]:06d}.jpg'"); open('list.txt','w').write('\n'.join(out))
PY
ffmpeg -y -loglevel error -f concat -safe 0 -i list.txt -vf "fps=30,format=yuv420p" -c:v libx264 -preset slow -crf 17 -movflags +faststart tbo-tracker-demo.mp4
rm -rf frames list.txt stamps.json; echo "Готово: promo/demo/tbo-tracker-demo.mp4 (таймкоды шагов — timeline.json)"
