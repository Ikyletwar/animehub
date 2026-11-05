#!/usr/bin/env bash
# run.sh — launcher AnimeHub
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"
cd "$DIR"
VENV="$DIR/.venv"
PY="$VENV/bin/python"

if [ ! -x "$PY" ]; then
  echo "[*] membuat virtualenv di $VENV"
  python3 -m venv "$VENV"
  "$PY" -m pip install --upgrade pip
  "$PY" -m pip install -r "$ROOT/requirements.txt"
fi

PIDS=$(ss -lptn 'sport = :8000' 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true)
[ -n "$PIDS" ] && kill $PIDS 2>/dev/null && echo "[*] stop instance lama (pid: $PIDS)" || true
sleep 1

echo "[*] starting AnimeHub ..."
setsid nohup "$PY" server.py > "$DIR/_server.log" 2>&1 < /dev/null &
sleep 4

if curl -sS -o /dev/null http://127.0.0.1:8000/api/health 2>/dev/null; then
  echo "[✓] UP  ->  http://127.0.0.1:8000"
  echo "    (server log: $DIR/_server.log)"
else
  echo "[✗] gagal start. cek $DIR/_server.log"
  tail -20 "$DIR/_server.log" || true
  exit 1
fi
