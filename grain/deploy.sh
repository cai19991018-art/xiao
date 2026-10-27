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
  echo "已生成 .env。请填写 DEEPSEEK_API_KEY: nano .env  然后重新运行 bash deploy.sh"
  exit 0
fi
set -a; . ./.env; set +a
PORT=${APP_PORT:-8080}
mkdir -p data
if command -v ufw >/dev/null && ufw status | grep -q active; then
  ufw allow "$PORT"/tcp
  [ -n "$DOMAIN" ] && ufw allow 80,443/tcp
fi
if [ -n "$DOMAIN" ]; then
  docker compose --profile https up -d --build
  URL="https://$DOMAIN"
else
  docker compose --profile https stop caddy 2>/dev/null || true
  docker compose up -d --build
  URL="http://$(curl -s -4 --max-time 5 ifconfig.me || echo VPS的IP):$PORT"
fi
echo "部署完成"
echo "网页:    $URL"
echo "API Key: $API_KEY"
