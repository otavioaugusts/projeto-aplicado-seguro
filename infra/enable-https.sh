#!/usr/bin/env bash
# Emite o certificado Let's Encrypt de IP (perfil shortlived) e liga o HTTPS.
# Uso:
#   sudo ./infra/enable-https.sh --ip 203.0.113.10 --email aluno@exemplo.com
# Ensaio contra a CA de teste (não serve para o ssl.org):
#   sudo ./infra/enable-https.sh --ip 203.0.113.10 --email aluno@exemplo.com --staging
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Execute com sudo."
  exit 1
fi

APP_DIR="${APP_DIR:-/opt/projeto-aplicado}"
IP=""
EMAIL=""
STAGING=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ip)
      IP="${2:-}"
      shift 2
      ;;
    --email)
      EMAIL="${2:-}"
      shift 2
      ;;
    --staging)
      STAGING=1
      shift
      ;;
    *)
      echo "Uso: sudo $0 --ip ENDERECO_IPV4 --email EMAIL [--staging]"
      exit 1
      ;;
  esac
done

if [[ ! "$IP" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]]; then
  echo "Informe um IPv4 público em --ip."
  exit 1
fi
for i in 1 2 3 4; do
  octeto="${BASH_REMATCH[$i]}"
  if ((10#$octeto > 255)); then
    echo "IPv4 inválido."
    exit 1
  fi
done
if [[ ! "$EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; then
  echo "Informe um e-mail válido em --email. A Let's Encrypt usa essa conta."
  exit 1
fi
if [[ ! -x /opt/certbot/bin/certbot ]]; then
  echo "Certbot não está em /opt/certbot. Rode infra/provision.sh antes."
  exit 1
fi

versao="$(/opt/certbot/bin/certbot --version 2>&1 | awk 'NR==1 { print $2 }')"
python3 - "$versao" << 'PY'
import sys
partes = [int(p) for p in sys.argv[1].split(".")[:3]]
while len(partes) < 3:
    partes.append(0)
if partes < [5, 4, 0]:
    raise SystemExit(f"Certbot {sys.argv[1]} é anterior a 5.4.")
PY

install -d -m 755 /usr/local/sbin /etc/letsencrypt/renewal-hooks/deploy /var/www/html/.well-known/acme-challenge
cat > /usr/local/sbin/reload-nginx-cert.sh << 'EOF'
#!/bin/sh
systemctl reload nginx
EOF
chmod 755 /usr/local/sbin/reload-nginx-cert.sh
cp /usr/local/sbin/reload-nginx-cert.sh /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh

if [[ "$STAGING" -eq 1 ]]; then
  echo "ATENÇÃO: --staging pede um certificado que o navegador e o ssl.org NÃO confiam."
fi

if [[ ! -f "/etc/letsencrypt/live/${IP}/fullchain.pem" ]]; then
  argumentos=(
    certonly
    --non-interactive
    --agree-tos
    --no-eff-email
    --email "$EMAIL"
    --preferred-profile shortlived
    --key-type ecdsa
    --elliptic-curve secp256r1
    --webroot
    --webroot-path /var/www/html
    --ip-address "$IP"
    --deploy-hook /usr/local/sbin/reload-nginx-cert.sh
  )
  if [[ "$STAGING" -eq 1 ]]; then
    argumentos+=(--staging)
  fi
  /opt/certbot/bin/certbot "${argumentos[@]}"
else
  echo "Já existe certificado em /etc/letsencrypt/live/${IP}. Não vou emitir outro."
fi

CERT_NAME="$IP"
if [[ ! -f "/etc/letsencrypt/live/${CERT_NAME}/fullchain.pem" ]]; then
  caminho="$(/opt/certbot/bin/certbot certificates 2>/dev/null | awk '/Certificate Path:/ { print $3; exit }')"
  if [[ -n "$caminho" && -f "$caminho" ]]; then
    CERT_NAME="$(basename "$(dirname "$caminho")")"
  fi
fi
if [[ ! -f "/etc/letsencrypt/live/${CERT_NAME}/fullchain.pem" ]]; then
  echo "O Certbot terminou sem deixar fullchain.pem. Veja: certbot certificates"
  exit 1
fi

if [[ ! "$CERT_NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "Nome de certificado inesperado: ${CERT_NAME}"
  exit 1
fi

destino="/etc/nginx/sites-available/projeto-aplicado.conf"
copia="$(mktemp)"
tinha_conf=0
if [[ -f "$destino" ]]; then
  cp "$destino" "$copia"
  tinha_conf=1
fi
sed "s/__CERT_NAME__/${CERT_NAME}/g" "${APP_DIR}/infra/nginx/projeto-aplicado.conf" > "$destino"
if ! nginx -t; then
  if [[ "$tinha_conf" -eq 1 ]]; then
    cp "$copia" "$destino"
  else
    rm -f "$destino"
  fi
  rm -f "$copia"
  echo "nginx -t falhou. A configuração anterior foi restaurada."
  echo "Se a queixa for o grupo X25519MLKEM768, a VM não está no Ubuntu 26.04 com OpenSSL 3.5."
  exit 1
fi
rm -f "$copia"
systemctl reload nginx

marca="/opt/certbot/bin/certbot renew -q"
if ! grep -qF "$marca" /etc/crontab; then
  echo "0 0,12 * * * root /opt/certbot/bin/python -c 'import random; import time; time.sleep(random.random() * 3600)' && /opt/certbot/bin/certbot renew -q" >> /etc/crontab
fi

echo
echo "HTTPS no ar para https://${IP}/"
echo "Renovação: duas vezes por dia, em /etc/crontab, com espera aleatória de até uma hora."
echo "O gancho /usr/local/sbin/reload-nginx-cert.sh recarrega o Nginx quando um certificado novo entra."
if [[ "$STAGING" -eq 1 ]]; then
  echo "Este certificado é de staging. Rode de novo sem --staging para o ssl.org marcar Certificate Trusted: YES."
else
  echo "Confira no próprio servidor:"
  echo "  openssl version"
  echo "  echo | openssl s_client -connect ${IP}:443 -tls1_3 -groups X25519MLKEM768 2>&1 | grep -E 'Protocol|Cipher|group|Group|X25519'"
fi

if ! /opt/certbot/bin/certbot renew --dry-run; then
  echo "O certificado atual permanece. O ensaio de renovação falhou; rode de novo: certbot renew --dry-run"
fi
