#!/bin/bash
# 服务器一键安装（在项目根目录执行：bash deploy/install.sh）
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==== 1. 检查 Python ===="
if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3，请先在宝塔「软件商店」安装 Python 项目管理器，或："
  echo "  yum install -y python3 python3-pip  /  apt install -y python3 python3-venv python3-pip"
  exit 1
fi
python3 --version

echo "==== 2. 创建虚拟环境 ===="
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip
pip install -r backend/requirements.txt

echo "==== 3. 准备配置与目录 ===="
mkdir -p backend/data backend/logs
if [ ! -f backend/.env ]; then
  cp backend/.env.example backend/.env
  # 生成随机 JWT
  if command -v openssl >/dev/null 2>&1; then
    SECRET=$(openssl rand -hex 32)
    sed -i "s/^JWT_SECRET=.*/JWT_SECRET=$SECRET/" backend/.env
  fi
  echo "已生成 backend/.env，请编辑填入币安 API Key，并修改管理员密码"
else
  echo "已存在 backend/.env，跳过复制"
fi

chmod +x backend/start.sh

echo ""
echo "==== 安装完成 ===="
echo "下一步："
echo "1) 编辑配置: nano $ROOT/backend/.env"
echo "2) 宝塔 → 软件商店 → Supervisor/进程守护 → 添加守护进程"
echo "   名称: quant-bot"
echo "   启动用户: root（或 www）"
echo "   运行目录: $ROOT/backend"
echo "   启动命令: $ROOT/backend/start.sh"
echo "   或: $ROOT/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 3003"
echo "3) 放行安全组/防火墙端口 3003（或你在 .env 里改的 APP_PORT）"
echo "4) 浏览器打开: http://服务器IP:3003"
echo "5) 登录后在「交易所设置」填 API，确认模拟/实盘后再点启动"
