#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/venv/bin/python}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
WORKERS="${WORKERS:-1}"
RESTART_DELAY="${RESTART_DELAY:-3}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[ops-erp] 未找到 $PYTHON_BIN，请先创建虚拟环境或设置 PYTHON_BIN" >&2
  exit 1
fi

echo "[ops-erp] Python: $("$PYTHON_BIN" -V 2>&1)"

if ! "$PYTHON_BIN" -c "import fastapi, sqlalchemy, uvicorn" >/dev/null 2>&1; then
  echo "[ops-erp] 缺少依赖，请执行: $PYTHON_BIN -m pip install fastapi uvicorn sqlalchemy" >&2
  exit 1
fi

echo "[ops-erp] 执行 Python 语法检查..."
"$PYTHON_BIN" -m py_compile database.py models.py auth.py main.py

if command -v node >/dev/null 2>&1; then
  echo "[ops-erp] 执行前端 JavaScript 语法检查..."
  TMP_DIR="$(mktemp -d)"
  trap 'rm -rf "$TMP_DIR"' EXIT
  "$PYTHON_BIN" - "$TMP_DIR" <<'PY'
import re
import sys
from pathlib import Path

target = Path(sys.argv[1])
html = Path("index.html").read_text(encoding="utf-8")
blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
written = 0
for index, block in enumerate(blocks):
    if "tailwind.config" in block:
        continue
    (target / f"inline_{index}.js").write_text(block, encoding="utf-8")
    written += 1
print(f"inline script blocks: {written}")
PY
  for script_file in "$TMP_DIR"/*.js; do
    node --check "$script_file"
  done
else
  echo "[ops-erp] 未检测到 node，跳过 JS 语法检查" >&2
fi

echo "[ops-erp] 初始化数据库并植入种子账号..."
"$PYTHON_BIN" - <<'PY'
from database import Base, DB_PATH, SessionLocal, engine
import models  # noqa: F401
from main import seed_database

Base.metadata.create_all(bind=engine)
with SessionLocal() as db:
    shift = seed_database(db)
print(f"database: {DB_PATH}")
print(f"active shift: {shift.id} ({shift.shift_type})")
PY

if "$PYTHON_BIN" - "$PORT" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket() as sock:
    sock.settimeout(0.5)
    raise SystemExit(0 if sock.connect_ex(("127.0.0.1", port)) == 0 else 1)
PY
then
  echo "[ops-erp] 端口 $PORT 已被占用，请停止旧进程或设置 PORT 环境变量" >&2
  exit 1
fi

echo "[ops-erp] 服务启动: http://$HOST:$PORT"
echo "[ops-erp] 守护模式已开启，异常退出后 ${RESTART_DELAY}s 自动重启 (Ctrl+C 停止)"

child_pid=""
shutdown() {
  echo "[ops-erp] 收到退出信号，正在停止服务..."
  if [[ -n "$child_pid" ]] && kill -0 "$child_pid" 2>/dev/null; then
    kill -TERM "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  exit 0
}
trap shutdown INT TERM

while true; do
  "$PYTHON_BIN" -m uvicorn main:app --host "$HOST" --port "$PORT" --workers "$WORKERS" &
  child_pid=$!
  set +e
  wait "$child_pid"
  exit_code=$?
  set -e
  child_pid=""
  echo "[ops-erp] 服务进程退出 (code=$exit_code)，${RESTART_DELAY}s 后重启"
  sleep "$RESTART_DELAY"
done
