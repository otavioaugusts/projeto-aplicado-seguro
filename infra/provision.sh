#!/usr/bin/env bash
# Prepara uma VM Ubuntu 26.04 LTS recém-criada.
# Uso, a partir do clone em /opt/projeto-aplicado:
#   sudo ADMIN_CIDR=203.0.113.10/32 ./infra/provision.sh
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Execute com sudo."
  exit 1
fi

APP_DIR="${APP_DIR:-/opt/projeto-aplicado}"
APP_USER="${APP_USER:-ubuntu}"
REPO_URL="${REPO_URL:-https://github.com/otavioaugusts/projeto-aplicado-seguro.git}"
ADMIN_CIDR="${ADMIN_CIDR:-}"

validar_usuario() {
  [[ "$1" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]
}

validar_cidr() {
  local cidr="$1"
  [[ "$cidr" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})/([0-9]|[12][0-9]|3[0-2])$ ]] || return 1
  local i octeto
  for i in 1 2 3 4; do
    octeto="${BASH_REMATCH[$i]}"
    ((10#$octeto <= 255)) || return 1
  done
}

if ! validar_usuario "$APP_USER"; then
  echo "APP_USER inválido."
  exit 1
fi
if ! id "$APP_USER" >/dev/null 2>&1; then
  echo "O usuário ${APP_USER} não existe nesta VM."
  exit 1
fi
if ! validar_cidr "$ADMIN_CIDR"; then
  echo "Defina ADMIN_CIDR com o seu IPv4 e a máscara, por exemplo 203.0.113.10/32."
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  nginx \
  fail2ban \
  python3 \
  python3-venv \
  python3-pip \
  python3-dev \
  build-essential \
  libaugeas0 \
  ufw \
  ca-certificates \
  curl \
  git \
  openssl

if [[ ! -d "${APP_DIR}/.git" ]]; then
  git clone "$REPO_URL" "$APP_DIR"
fi

umask 022
chown -R "${APP_USER}:${APP_USER}" "$APP_DIR"
sudo -u "$APP_USER" python3 -m venv "${APP_DIR}/.venv"
sudo -u "$APP_USER" "${APP_DIR}/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
chmod -R a+rX "$APP_DIR"
if [[ -f "${APP_DIR}/.env" ]]; then
  chown root:root "${APP_DIR}/.env"
  chmod 600 "${APP_DIR}/.env"
fi

python3 -m venv /opt/certbot
/opt/certbot/bin/pip install --upgrade pip
/opt/certbot/bin/pip install "certbot>=5.4"
ln -sfn /opt/certbot/bin/certbot /usr/local/bin/certbot
versao="$(/opt/certbot/bin/certbot --version 2>&1 | awk 'NR==1 { print $2 }')"
python3 - "$versao" << 'PY'
import sys
partes = [int(p) for p in sys.argv[1].split(".")[:3]]
while len(partes) < 3:
    partes.append(0)
if partes < [5, 4, 0]:
    raise SystemExit(f"Certbot {sys.argv[1]} é anterior a 5.4.")
print(f"Certbot {sys.argv[1]} atende o escopo.")
PY

install -d -m 755 /var/www/html/.well-known/acme-challenge
install -m 644 "${APP_DIR}/infra/nginx/conf.d-hardening.conf" /etc/nginx/conf.d/hardening.conf
install -m 644 "${APP_DIR}/infra/nginx/projeto-aplicado-http.conf" /etc/nginx/sites-available/projeto-aplicado.conf
ln -sfn /etc/nginx/sites-available/projeto-aplicado.conf /etc/nginx/sites-enabled/projeto-aplicado.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx
systemctl reload nginx

install -m 644 "${APP_DIR}/infra/ssh/99-hardening.conf" /etc/ssh/sshd_config.d/99-hardening.conf
sshd -t
if ! systemctl reload ssh; then
  systemctl reload sshd || systemctl restart ssh || systemctl restart sshd
fi

install -m 644 "${APP_DIR}/infra/fail2ban/jail.d-sshd.local" /etc/fail2ban/jail.d/zz-sshd.local
systemctl enable --now fail2ban
systemctl restart fail2ban

sed "s/^ubuntu /${APP_USER} /" "${APP_DIR}/infra/sudoers/projeto-aplicado" > /etc/sudoers.d/projeto-aplicado
chmod 440 /etc/sudoers.d/projeto-aplicado
visudo -cf /etc/sudoers.d/projeto-aplicado

install -m 644 "${APP_DIR}/infra/systemd/projeto-aplicado.service" /etc/systemd/system/projeto-aplicado.service
systemctl daemon-reload
systemctl enable projeto-aplicado

ufw default deny incoming
ufw default allow outgoing
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow from "$ADMIN_CIDR" to any port 22 proto tcp
if [[ -n "${EXTRA_SSH_CIDRS:-}" ]]; then
  IFS=',' read -ra _cidrs <<< "${EXTRA_SSH_CIDRS}"
  for cidr in "${_cidrs[@]}"; do
    cidr="${cidr// /}"
    if ! validar_cidr "$cidr"; then
      echo "CIDR inválido em EXTRA_SSH_CIDRS: ${cidr}"
      exit 1
    fi
    ufw allow from "$cidr" to any port 22 proto tcp
  done
fi
ip_peer="$(ss -Htn state established '( sport = :22 )' 2>/dev/null | awk 'NR==1 {
  peer = $NF
  sub(/:[0-9]+$/, "", peer)
  gsub(/[][]/, "", peer)
  print peer
}' || true)"
if [[ -n "$ip_peer" && "$ADMIN_CIDR" != "${ip_peer}/32" ]]; then
  echo "A sessão SSH atual vem de ${ip_peer}, mas ADMIN_CIDR é ${ADMIN_CIDR}."
  echo "Parei antes de ligar o ufw para não derrubar esta conexão."
  echo "Rode de novo com ADMIN_CIDR=${ip_peer}/32"
  exit 1
fi
ufw --force enable

echo
echo "Base pronta. O Nginx ainda está só em HTTP, de propósito."
echo "Próximos passos:"
echo "  1. sudo python3 ${APP_DIR}/scripts/init_env.py"
echo "  2. sudo systemctl start projeto-aplicado"
echo "  3. sudo ${APP_DIR}/infra/enable-https.sh --ip IP_PUBLICO --email seu-email"
echo "Confira a autenticação por chave em outro terminal antes de fechar esta sessão."
echo "sshd efetivo: $(sshd -T | awk '/^passwordauthentication / { print }')"
