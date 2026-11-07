#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/venv/bin/python}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
WORKERS="${WORKERS:-1}"
RESTART_DELAY="${RESTART_DELAY:-3}"
ACTION="${1:-start}"
RUN_DIR="$ROOT_DIR/run"
PID_FILE="$RUN_DIR/ops_erp.pid"
LOG_FILE="$RUN_DIR/ops_erp.log"

mkdir -p "$RUN_DIR"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[ops-erp] 未找到 $PYTHON_BIN，请先创建虚拟环境或设置 PYTHON_BIN" >&2
  exit 1
fi

check_syntax() {
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
}

init_database() {
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
}

port_in_use() {
  "$PYTHON_BIN" - "$PORT" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket() as sock:
    sock.settimeout(0.5)
    raise SystemExit(0 if sock.connect_ex(("127.0.0.1", port)) == 0 else 1)
PY
}

read_pid() {
  [[ -f "$PID_FILE" ]] && tr -d '[:space:]' < "$PID_FILE" || true
}

is_running() {
  local pid
  pid="$(read_pid)"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

stop_service() {
  local pid
  pid="$(read_pid)"
  if [[ -z "$pid" ]]; then
    echo "[ops-erp] 未发现后台服务记录"
    return 0
  fi
  if kill -0 "$pid" 2>/dev/null; then
    echo "[ops-erp] 正在停止服务 (PID $pid)..."
    kill -TERM "$pid" 2>/dev/null || true
    for _ in {1..30}; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.2
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
  fi
  rm -f "$PID_FILE"
  echo "[ops-erp] 服务已停止"
}

start_background() {
  if is_running; then
    echo "[ops-erp] 服务已在运行 (PID $(read_pid))，访问 http://$HOST:$PORT"
    return 0
  fi
  if port_in_use; then
    echo "[ops-erp] 端口 $PORT 已被其他进程占用，请先停止它或设置 PORT" >&2
    exit 1
  fi
  echo "[ops-erp] 正在后台启动，日志: $LOG_FILE"
  nohup setsid "$0" foreground >>"$LOG_FILE" 2>&1 < /dev/null &
  local launcher_pid=$!
  echo "$launcher_pid" > "$PID_FILE"
  for _ in {1..60}; do
    if ! kill -0 "$launcher_pid" 2>/dev/null; then
      break
    fi
    if port_in_use; then
      echo "[ops-erp] 服务已启动 (PID $launcher_pid)：http://$HOST:$PORT"
      return 0
    fi
    sleep 0.25
  done
  echo "[ops-erp] 启动失败，请查看日志: $LOG_FILE" >&2
  tail -30 "$LOG_FILE" >&2 || true
  rm -f "$PID_FILE"
  exit 1
}

case "$ACTION" in
  start)
    echo "[ops-erp] Python: $("$PYTHON_BIN" -V 2>&1)"
    if ! "$PYTHON_BIN" -c "import fastapi, sqlalchemy, uvicorn" >/dev/null 2>&1; then
      echo "[ops-erp] 缺少依赖，请执行: $PYTHON_BIN -m pip install fastapi uvicorn sqlalchemy" >&2
      exit 1
    fi
    check_syntax
    init_database
    start_background
    exit 0
    ;;
  stop)
    stop_service
    exit 0
    ;;
  restart)
    stop_service
    exec "$0" start
    ;;
  status)
    if is_running; then
      echo "[ops-erp] 运行中 (PID $(read_pid))：http://$HOST:$PORT"
    else
      echo "[ops-erp] 未运行"
      exit 3
    fi
    exit 0
    ;;
  foreground)
    ;;
  *)
    echo "用法: $0 {start|stop|restart|status|foreground}" >&2
    exit 2
    ;;
esac

echo "[ops-erp] 服务启动: http://$HOST:$PORT"
echo "[ops-erp] 守护模式已开启，异常退出后 ${RESTART_DELAY}s 自动重启"

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
