#!/bin/bash
# 宝塔「进程守护」启动脚本 — 量化机器人
# 工作目录请设为：本文件所在目录的上一级 backend（或项目里的 backend）
set -e
cd "$(dirname "$0")"

# 优先使用 venv
if [ -f "../.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "../.venv/bin/activate"
elif [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

export PYTHONUNBUFFERED=1
mkdir -p data logs

# 从 .env 读端口，默认 3003
PORT=3003
if [ -f .env ]; then
  ENV_PORT=$(grep -E '^APP_PORT=' .env | head -1 | cut -d= -f2 | tr -d ' \r')
  if [ -n "$ENV_PORT" ]; then
    PORT="$ENV_PORT"
  fi
fi

exec python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --workers 1
