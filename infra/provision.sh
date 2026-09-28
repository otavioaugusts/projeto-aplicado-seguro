#!/usr/bin/env bash
# Prepara uma VM Ubuntu 26.04 LTS.
# Rode como root, de preferência a partir do clone do branch que deve subir:
#   sudo bash infra/provision.sh
# A porta 22 fica aberta. A proteção é chave SSH e Fail2Ban (4 tentativas, 24h).
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Execute com sudo."
  exit 1
fi

APP_DIR="${APP_DIR:-/opt/projeto-aplicado}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
REPO_URL="${REPO_URL:-https://github.com/otavioaugusts/projeto-aplicado-seguro.git}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_REPO="$(cd "${SCRIPT_DIR}/.." && pwd)"

validar_usuario() {
  [[ "$1" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]
}

validar_ref() {
  [[ "$1" =~ ^[A-Za-z0-9._/-]+$ ]]
}

if ! validar_usuario "$DEPLOY_USER"; then
  echo "DEPLOY_USER inválido."
  exit 1
fi
if [[ -n "${GIT_REF:-}" ]] && ! validar_ref "$GIT_REF"; then
  echo "GIT_REF inválida."
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

clonar_aplicacao() {
  if [[ -d "${APP_DIR}/.git" ]]; then
    return
  fi
  mkdir -p "$(dirname "$APP_DIR")"
  local remote="" branch=""
  if [[ -d "${SOURCE_REPO}/.git" && "${SOURCE_REPO}" != "${APP_DIR}" ]]; then
    remote="$(git -C "$SOURCE_REPO" config --get remote.origin.url || true)"
    branch="$(git -C "$SOURCE_REPO" rev-parse --abbrev-ref HEAD)"
  fi
  remote="${remote:-$REPO_URL}"
  if [[ -n "${GIT_REF:-}" ]]; then
    git clone --branch "$GIT_REF" "$remote" "$APP_DIR"
  elif [[ -n "$branch" && "$branch" != "HEAD" ]]; then
    git clone --branch "$branch" "$remote" "$APP_DIR"
  else
    git clone "$remote" "$APP_DIR"
    if [[ -n "$branch" && "$branch" == "HEAD" ]]; then
      git -C "$APP_DIR" checkout "$(git -C "$SOURCE_REPO" rev-parse HEAD)"
    fi
  fi
}

clonar_aplicacao

if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash --user-group "$DEPLOY_USER"
fi
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/${DEPLOY_USER}/.ssh"
if [[ ! -f "/home/${DEPLOY_USER}/.ssh/authorized_keys" ]]; then
  install -m 600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /dev/null "/home/${DEPLOY_USER}/.ssh/authorized_keys"
fi
chown "$DEPLOY_USER:$DEPLOY_USER" "/home/${DEPLOY_USER}/.ssh" "/home/${DEPLOY_USER}/.ssh/authorized_keys"
chmod 700 "/home/${DEPLOY_USER}/.ssh"
chmod 600 "/home/${DEPLOY_USER}/.ssh/authorized_keys"

umask 022
chown -R "${DEPLOY_USER}:${DEPLOY_USER}" "$APP_DIR"
sudo -u "$DEPLOY_USER" python3 -m venv "${APP_DIR}/.venv"
sudo -u "$DEPLOY_USER" "${APP_DIR}/.venv/bin/pip" install --upgrade pip
sudo -u "$DEPLOY_USER" "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
find "$APP_DIR" -path "${APP_DIR}/.env" -prune -o -exec chmod a+rX {} +
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

# O nginx.conf do Ubuntu 26.04 já traz `server_tokens build` no contexto http.
# Outro server_tokens no mesmo contexto (conf.d) faz o nginx -t abortar.
rm -f /etc/nginx/conf.d/hardening.conf
if [[ -f /etc/nginx/nginx.conf ]]; then
  sed -i -E 's/^([[:space:]]*)(server_tokens[[:space:]].*)$/# \1\2/' /etc/nginx/nginx.conf
fi

install -d -m 755 /var/www/html/.well-known/acme-challenge
if [[ ! -f /etc/nginx/sites-available/projeto-aplicado.conf ]]; then
  install -m 644 "${APP_DIR}/infra/nginx/projeto-aplicado-http.conf" /etc/nginx/sites-available/projeto-aplicado.conf
fi
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

sed "s/^deploy /${DEPLOY_USER} /" "${APP_DIR}/infra/sudoers/projeto-aplicado" > /etc/sudoers.d/projeto-aplicado
chmod 440 /etc/sudoers.d/projeto-aplicado
visudo -cf /etc/sudoers.d/projeto-aplicado

install -m 644 "${APP_DIR}/infra/systemd/projeto-aplicado.service" /etc/systemd/system/projeto-aplicado.service
systemctl daemon-reload
systemctl enable projeto-aplicado

ufw default deny incoming
ufw default allow outgoing
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 22/tcp
ufw --force enable

echo
echo "Base pronta. O Nginx ainda está só em HTTP, de propósito, se o certificado não existir."
echo "A porta 22 está aberta no UFW. Senha de SSH desligada. Fail2Ban: maxretry 4, bantime 24h."
echo "Usuário de deploy: ${DEPLOY_USER}"
echo "  chave pública em /home/${DEPLOY_USER}/.ssh/authorized_keys"
echo "  secret VM_USER do GitHub = ${DEPLOY_USER}"
echo "  sudo desse usuário só reinicia o serviço projeto-aplicado"
echo "Próximos passos:"
echo "  1. sudo python3 ${APP_DIR}/scripts/init_env.py"
echo "  2. sudo systemctl start projeto-aplicado"
echo "  3. sudo ${APP_DIR}/infra/enable-https.sh --ip IP_PUBLICO --email seu-email"
echo "Confira a autenticação por chave em outro terminal antes de fechar esta sessão."
echo "sshd efetivo: $(sshd -T | awk '/^passwordauthentication / { print }')"
