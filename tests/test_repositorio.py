import re
import subprocess
from pathlib import Path

import yaml

from app import POLITICA_CSP

RAIZ = Path(__file__).resolve().parent.parent

HASH_BCRYPT = re.compile(r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{20,}")
CHAVE_PRIVADA = re.compile(r"-----BEGIN (?:OPENSSH |RSA |EC |OPENSSH )?PRIVATE KEY-----")
AWS_ACCESS = re.compile(r"AKIA[0-9A-Z]{16}")


def test_gitignore_protege_env_e_chaves():
    ignorados = subprocess.run(
        ["git", "check-ignore", "-v", ".env", ".venv", "segredo.pem", "app.sqlite", "id_ed25519"],
        cwd=RAIZ,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert ".env" in ignorados
    assert ".venv" in ignorados
    exemplo = subprocess.run(
        ["git", "check-ignore", ".env.example"],
        cwd=RAIZ,
        capture_output=True,
        text=True,
    )
    assert exemplo.returncode != 0


def test_exemplo_de_env_nao_tem_segredo():
    texto = (RAIZ / ".env.example").read_text(encoding="utf-8")
    assert "substitua-pelo-hash-bcrypt" in texto
    assert HASH_BCRYPT.search(texto) is None
    assert "BEGIN" not in texto


def test_arvore_nao_tem_segredo_real():
    pulados = {".git", ".venv", "venv", "__pycache__", ".pytest_cache"}
    for caminho in RAIZ.rglob("*"):
        if not caminho.is_file() or pulados.intersection(caminho.parts):
            continue
        if caminho.suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            continue
        texto = caminho.read_text(encoding="utf-8", errors="ignore")
        assert HASH_BCRYPT.search(texto) is None, caminho
        assert CHAVE_PRIVADA.search(texto) is None, caminho
        assert AWS_ACCESS.search(texto) is None, caminho


def test_workflow_de_deploy_e_valido():
    caminho = RAIZ / ".github" / "workflows" / "deploy.yml"
    documento = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    gatilho = documento.get(True) or documento.get("on")
    assert "main" in gatilho["push"]["branches"]
    publicar = documento["jobs"]["publicar"]
    assert publicar["needs"] == "testar"
    texto = caminho.read_text(encoding="utf-8")
    for segredo in ("VM_HOST", "VM_USER", "VM_SSH_KEY", "VM_SSH_KNOWN_HOSTS"):
        assert f"secrets.{segredo}" in texto
    assert "StrictHostKeyChecking=yes" in texto
    assert "appleboy" not in texto
    assert "sshpass" not in texto


def test_nginx_csp_igual_a_da_aplicacao_e_tem_pqc():
    nginx = (RAIZ / "infra" / "nginx" / "projeto-aplicado.conf").read_text(encoding="utf-8")
    assert POLITICA_CSP in nginx
    assert "X25519MLKEM768" in nginx
    assert "return 301 https://$host$request_uri;" in nginx
    assert "fullchain.pem" in nginx
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in nginx
    assert "TLSv1.1" not in nginx
    assert "TLSv1.0" not in nginx
    assert "proxy_hide_header Strict-Transport-Security;" in nginx
    assert "ssl_stapling" not in nginx


def test_fail2ban_e_sshd_batem_com_o_escopo():
    jail = (RAIZ / "infra" / "fail2ban" / "jail.d-sshd.local").read_text(encoding="utf-8")
    assert "maxretry = 4" in jail
    assert "bantime = 24h" in jail
    sshd = (RAIZ / "infra" / "ssh" / "99-hardening.conf").read_text(encoding="utf-8")
    assert "PasswordAuthentication no" in sshd
    assert "AuthenticationMethods publickey" in sshd
    assert "PermitRootLogin no" in sshd


def test_ssh_aberto_com_fail2ban_e_usuario_deploy():
    provision = (RAIZ / "infra" / "provision.sh").read_text(encoding="utf-8")
    assert "ufw allow 22/tcp" in provision
    assert "ufw allow from" not in provision
    assert "Defina ADMIN_CIDR" not in provision
    sudoers = (RAIZ / "infra" / "sudoers" / "projeto-aplicado").read_text(encoding="utf-8")
    assert sudoers.startswith("deploy ")
    assert "systemctl restart projeto-aplicado" in sudoers
    assert "NOPASSWD: ALL" not in sudoers
    deploy = (RAIZ / "infra" / "remote-deploy.sh").read_text(encoding="utf-8")
    assert "origin/main" in deploy


def test_scripts_de_infra_tem_sintaxe_bash():
    for nome in ("provision.sh", "enable-https.sh", "remote-deploy.sh"):
        caminho = RAIZ / "infra" / nome
        subprocess.run(["bash", "-n", str(caminho)], check=True)
