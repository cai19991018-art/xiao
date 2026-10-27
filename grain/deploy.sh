#!/usr/bin/env bash
# Ubuntu 22.04 一键部署: bash deploy.sh
set -e
cd "$(dirname "$0")"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s/^API_KEY=.*/API_KEY=$(openssl rand -hex 24)/" .env
  echo "已生成 .env, 请先编辑域名: nano .env  然后重新运行 bash deploy.sh"
  exit 0
fi
mkdir -p data
command -v ufw >/dev/null && ufw status | grep -q active && ufw allow 80,443/tcp || true
docker compose up -d --build
echo "部署完成。API Key: $(grep ^API_KEY .env | cut -d= -f2)"
echo "网页: $(grep ^PUBLIC_URL .env | cut -d= -f2)"
