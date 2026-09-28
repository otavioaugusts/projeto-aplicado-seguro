#!/usr/bin/env bash
# Atualiza o código na VM. O GitHub Actions manda este arquivo pela sessão SSH.
# Não apaga o .env nem o .venv: os dois estão fora do Git.
set -euo pipefail
umask 022

APP_DIR="${APP_DIR:-/opt/projeto-aplicado}"

if [[ ! -d "${APP_DIR}/.git" ]]; then
  echo "Diretório ${APP_DIR} ainda não é um clone. Rode infra/provision.sh."
  exit 1
fi
if [[ ! -f "${APP_DIR}/.env" ]]; then
  echo "Falta ${APP_DIR}/.env. Rode sudo python3 ${APP_DIR}/scripts/init_env.py antes do deploy."
  exit 1
fi

cd "$APP_DIR"
git fetch origin main
git reset --hard origin/main
if [[ ! -x "${APP_DIR}/.venv/bin/pip" ]]; then
  python3 -m venv "${APP_DIR}/.venv"
fi
"${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

sudo /usr/bin/systemctl restart projeto-aplicado

for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS http://127.0.0.1:8000/saude >/dev/null; then
    echo "Aplicação respondeu em /saude."
    exit 0
  fi
  sleep 1
done

echo "A aplicação não respondeu em /saude." >&2
systemctl status projeto-aplicado --no-pager >&2 || true
exit 1
