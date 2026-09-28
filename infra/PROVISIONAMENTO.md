# Roteiro da VM

A instância que está no ar:

- AWS, região `sa-east-1`, tipo `t3.micro`
- Ubuntu Server 26.04 LTS
- Elastic IP `18.228.27.223` — https://18.228.27.223
- Nginx 1.28.3, OpenSSL 3.5.5, Certbot 5.8.0
- Certificado Let's Encrypt de IP, perfil `shortlived`, grupo `X25519MLKEM768` negociado

O 24.04 LTS fica de fora de uma instalação nova: o OpenSSL de lá é 3.0 e o Nginx recusa o grupo `X25519MLKEM768`. O 26.04 já traz OpenSSL 3.5.

## 1. Console da AWS

1. EC2 → Launch instance.
2. AMI: **Ubuntu Server 26.04 LTS**, 64-bit (x86), Canonical.
3. Tipo: `t3.micro`. Disco gp3 de 20 GB. Região `sa-east-1` nesta entrega.
4. Par de chaves para o acesso administrativo (`ubuntu`). O `.pem` não entra no Git.
5. Security group:
   - TCP 22, origem `0.0.0.0/0` e `::/0`
   - TCP 80, origem `0.0.0.0/0` e `::/0`
   - TCP 443, origem `0.0.0.0/0` e `::/0`
6. Elastic IP associado à instância, para o certificado de IP não mudar a cada desligamento.

A porta 22 fica aberta de propósito. Quem entra precisa de chave: `PasswordAuthentication no` e `AuthenticationMethods publickey`. O Fail2Ban bane o IP depois de 4 falhas, por 24 horas. Restringir o 22 a um único `/32` não aguenta IP de administração que muda, nem o GitHub Actions.

## 2. Primeiro acesso

```bash
ssh -i caminho/da/chave.pem ubuntu@18.228.27.223
```

Confirme o acesso num segundo terminal antes de seguir. O script desliga senha de SSH.

## 3. Script de base

Clone o branch que deve ser instalado e rode o script a partir desse clone. Ele leva para `/opt/projeto-aplicado` o branch do checkout, não força `main`. O deploy contínuo, depois do merge, continua rastreando `origin/main` (`infra/remote-deploy.sh`).

```bash
sudo apt-get update
sudo apt-get install -y git
git clone --branch BRANCH https://github.com/otavioaugusts/projeto-aplicado-seguro.git
sudo bash projeto-aplicado-seguro/infra/provision.sh
```

O script:

- comenta `server_tokens` em `/etc/nginx/nginx.conf` (no 26.04 a linha é `server_tokens build;` e um segundo `server_tokens` em `conf.d` derruba o `nginx -t`) e apaga `/etc/nginx/conf.d/hardening.conf` se existir;
- cria o usuário `deploy`, com sudo só para `systemctl restart projeto-aplicado`;
- abre 22, 80 e 443 no UFW;
- instala Fail2Ban (`maxretry` 4, `bantime` 24h) e o drop-in de SSH só por chave.

`ADMIN_CIDR` não é mais usado.

## 4. Chave do deploy

O secret `VM_USER` deve ser `deploy`, não `ubuntu`. O `ubuntu` da AMI tem `NOPASSWD: ALL` via cloud-init; usar essa conta no Actions entrega root a quem tiver a chave de deploy.

No seu computador, a partir da chave privada que está no secret `VM_SSH_KEY`:

```bash
ssh-keygen -y -f deploy_key
```

Na VM:

```bash
sudo install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
sudo tee /home/deploy/.ssh/authorized_keys >/dev/null
sudo chown deploy:deploy /home/deploy/.ssh/authorized_keys
sudo chmod 600 /home/deploy/.ssh/authorized_keys
```

Cole a linha pública no `authorized_keys`. Teste `ssh -i deploy_key deploy@18.228.27.223` antes de trocar o secret.

## 5. Usuário da aplicação

```bash
sudo python3 /opt/projeto-aplicado/scripts/init_env.py
sudo systemctl start projeto-aplicado
curl -fsS http://127.0.0.1:8000/saude
```

O `.env` fica root:root 600. O systemd lê esse arquivo. O Gunicorn, como `www-data`, não abre o `.env`: se o arquivo não for legível, `carregar_env()` segue com as variáveis que o processo já recebeu. Não faça `source .env`.

## 6. Certificado no IP e HTTPS

```bash
sudo /opt/projeto-aplicado/infra/enable-https.sh \
  --ip 18.228.27.223 \
  --email seu-email@exemplo.com
```

O comando equivalente, sem `--staging`:

```bash
sudo /opt/certbot/bin/certbot certonly \
  --non-interactive \
  --agree-tos \
  --no-eff-email \
  --email seu-email@exemplo.com \
  --preferred-profile shortlived \
  --key-type ecdsa \
  --elliptic-curve secp256r1 \
  --webroot \
  --webroot-path /var/www/html \
  --ip-address 18.228.27.223
```

Nesta VM o Certbot é o 5.8.0. O perfil `shortlived` vale cerca de 6 dias. A renovação entra em `/etc/crontab` duas vezes por dia, com espera aleatória de até uma hora. Um único gancho, `/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh`, recarrega o Nginx. Não há `--deploy-hook` na emissão, para o reload não acontecer duas vezes.

## 7. Conferir

```bash
nginx -v
openssl version
certbot --version
sudo sshd -T | grep -E 'passwordauthentication|permitrootlogin|pubkeyauthentication'
sudo fail2ban-client status sshd
sudo ufw status
echo | openssl s_client -connect 18.228.27.223:443 -tls1_3 -groups X25519MLKEM768 2>&1 | grep -E 'Protocol|Cipher|group|Group|X25519'
curl -fsS https://18.228.27.223/saude
```

O que já foi conferido nesta instância: HTTPS com o certificado de IP, negociação `X25519MLKEM768`, Fail2Ban, SSH só por chave e UFW. Os prints do ssl.org e da DigiCert ainda vão para `docs/img/`.
