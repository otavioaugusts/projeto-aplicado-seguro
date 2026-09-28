# Roteiro da VM

Ubuntu Server **26.04 LTS** (Resolute), na AWS, tipo `t3.micro`. O 24.04 LTS fica de fora: o OpenSSL de lá é 3.0 e o Nginx recusa o grupo `X25519MLKEM768`. O 26.04 já vem com OpenSSL 3.5.5 e Nginx 1.28, que negociam a troca de chaves híbrida pedida no checker da DigiCert.

O detalhe de cada escolha está no `README.md`. Aqui é a sequência de comando.

## 1. Console da AWS

1. EC2 → Launch instance.
2. AMI: **Ubuntu Server 26.04 LTS**, 64-bit (x86). Publicação da Canonical.
3. Tipo: `t3.micro` (ou `t2.micro`, se o free tier da conta só listar esse). Disco gp3 de 20 GB.
4. Par de chaves novo. Baixe o `.pem` uma vez. Ele não entra no Git.
5. Security group, só estas regras:
   - TCP 22, origem = o seu IP `/32` (opção My IP).
   - TCP 80, origem `0.0.0.0/0` e `::/0`.
   - TCP 443, origem `0.0.0.0/0` e `::/0`.
6. Anote o IP público. Não associe um IP elástico se a conta não tiver cota grátis; o IP público da instância serve. Se a instância for desligada, o IP pode mudar e o certificado de IP precisa ser emitido de novo.

A porta 22 não fica aberta para a internet. O workflow do GitHub Actions também entra por SSH, então, depois que o deploy for necessário, inclua as faixas publicadas em `https://api.github.com/meta` (campo `actions`) na regra da porta 22. A chave continua obrigatória e o Fail2Ban continua valendo. Sem essas faixas, o push em `main` não alcança a VM.

## 2. Primeiro acesso

No seu computador, com o `.pem` de permissão 600:

```bash
ssh -i caminho/da/chave.pem ubuntu@IP_PUBLICO
```

Confirme que entrou sem senha, num segundo terminal, antes de seguir. O script desliga senha de SSH.

## 3. Script de base

O `ADMIN_CIDR` é o IP **de onde você está conectado**, com `/32`. Não é o IP da VM. O script confere isso e para antes de ligar o firewall se os dois não baterem.

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/otavioaugusts/projeto-aplicado-seguro.git
sudo ADMIN_CIDR=SEU_IP/32 bash projeto-aplicado-seguro/infra/provision.sh
```

Isso instala Nginx, Fail2Ban, UFW, o aplicativo em `/opt/projeto-aplicado` e o Certbot 5.4+ em `/opt/certbot`. O site ainda responde só em HTTP, para o desafio do Let's Encrypt ter onde cair.

Faixas extras de SSH, se for o caso do Actions:

```bash
sudo ADMIN_CIDR=SEU_IP/32 EXTRA_SSH_CIDRS=203.0.113.0/24,198.51.100.0/24 \
  bash /opt/projeto-aplicado/infra/provision.sh
```

## 4. Usuário de demonstração

```bash
sudo python3 /opt/projeto-aplicado/scripts/init_env.py
sudo systemctl start projeto-aplicado
curl -fsS http://127.0.0.1:8000/saude
```

O script pede usuário e senha (mínimo 12 caracteres) e grava só o hash bcrypt em `/opt/projeto-aplicado/.env`, modo 600, dono root. Não faça `source .env`: o shell come o `$` do hash. Quem lê esse arquivo é o systemd.

## 5. Certificado no IP e HTTPS

Sem `--staging`. O staging não é confiável e o ssl.org marcaria o certificado como não confiado.

```bash
sudo /opt/projeto-aplicado/infra/enable-https.sh \
  --ip IP_PUBLICO \
  --email seu-email@exemplo.com
```

O comando que o script executa, no essencial, é o do Certbot 5.4+ para certificado de endereço IP:

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
  --ip-address IP_PUBLICO \
  --deploy-hook /usr/local/sbin/reload-nginx-cert.sh
```

- `--preferred-profile shortlived` é obrigatório para certificado de IP. A validade fica em torno de 6 dias (160 horas).
- `--webroot` porque o plugin do Nginx ainda não emite certificado de IP.
- ECDSA P-256 (`secp256r1`) com a cadeia em `fullchain.pem`, para o checker ver assinatura boa, chave aceitável e Certificate Trusted: YES.
- A renovação entra em `/etc/crontab` duas vezes por dia (00:00 e 12:00), com espera aleatória de até uma hora, no mesmo desenho da documentação do Certbot via pip. O gancho `/usr/local/sbin/reload-nginx-cert.sh` dá `systemctl reload nginx` quando um certificado novo é gravado. Há uma cópia em `/etc/letsencrypt/renewal-hooks/deploy/`.

Ensaio da renovação:

```bash
sudo /opt/certbot/bin/certbot renew --dry-run
```

## 6. Conferir na própria VM

```bash
openssl version
sudo sshd -T | grep -E 'passwordauthentication|permitrootlogin|pubkeyauthentication'
sudo fail2ban-client status sshd
sudo ufw status
echo | openssl s_client -connect IP_PUBLICO:443 -tls1_3 -groups X25519MLKEM768 2>&1 | grep -E 'Protocol|Cipher|group|Group|X25519'
curl -fsS "https://IP_PUBLICO/saude"
```

`openssl version` precisa mostrar 3.5 ou mais novo. A saída do `s_client` precisa citar `X25519MLKEM768`. `passwordauthentication` precisa estar `no`. O Fail2Ban do sshd precisa mostrar `maxretry` 4 e ban de 24 horas.

TLS 1.0 e 1.1 não devem completar:

```bash
openssl s_client -connect IP_PUBLICO:443 -tls1_1 </dev/null
```

## 7. Secrets do GitHub e o primeiro deploy

No repositório: Settings → Secrets and variables → Actions.

| Secret | Conteúdo |
| --- | --- |
| `VM_HOST` | IP público, sem `https://` |
| `VM_USER` | `ubuntu` |
| `VM_SSH_KEY` | chave privada usada no deploy, o PEM inteiro |
| `VM_SSH_KNOWN_HOSTS` | saída de `ssh-keyscan IP_PUBLICO` |

A chave pública correspondente entra em `/home/ubuntu/.ssh/authorized_keys`. Pode ser a mesma do acesso administrativo ou um par só do Actions.

```bash
ssh-keyscan IP_PUBLICO
```

Copie as linhas para o secret `VM_SSH_KNOWN_HOSTS`. O workflow recusa conectar se a chave do host não estiver lá.

Um push em `main` roda os testes e, se passarem, entra por SSH e executa `infra/remote-deploy.sh`: `git fetch`, `git reset --hard origin/main`, `pip install` e `systemctl restart projeto-aplicado`. O `.env` não é apagado.

## 8. Prints para o relatório

- https://www.ssl.org/ com o IP. Meta: Certificate Trusted: YES e Algorithm / Key Type & Size: Good signature · Acceptable key.
- https://www.digicert.com/pqc-checker com o mesmo IP, mostrando `X25519MLKEM768`.

Guarde as imagens em `docs/evidencias/` e preencha o IP no README.
