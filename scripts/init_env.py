#!/usr/bin/env python3
"""Cria o .env local com hash bcrypt e uma chave aleatória.

Não imprime a senha nem o hash. O arquivo gerado está no .gitignore.
"""

from __future__ import annotations

import getpass
import re
import secrets
import sys
from pathlib import Path

import bcrypt

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / ".env"
USUARIO_OK = re.compile(r"[A-Za-z0-9._-]{1,64}")


def main() -> int:
    if DESTINO.exists():
        print(f"{DESTINO} já existe. Não vou sobrescrever.", file=sys.stderr)
        return 1

    usuario = input("Usuário de demonstração [aluno]: ").strip() or "aluno"
    if not USUARIO_OK.fullmatch(usuario):
        print("Usuário inválido. Use letras, números, ponto, _ e -.", file=sys.stderr)
        return 1

    senha = getpass.getpass("Senha (mínimo 12 caracteres): ")
    confirmacao = getpass.getpass("Repita a senha: ")
    if senha != confirmacao:
        print("As senhas não conferem.", file=sys.stderr)
        return 1
    if len(senha) < 12:
        print("Use pelo menos 12 caracteres.", file=sys.stderr)
        return 1
    if len(senha.encode("utf-8")) > 72:
        print("O bcrypt só considera 72 bytes. Escolha uma senha mais curta.", file=sys.stderr)
        return 1

    hash_senha = bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    chave = secrets.token_urlsafe(48)
    conteudo = (
        f"DEMO_USERNAME={usuario}\n"
        f"DEMO_PASSWORD_HASH={hash_senha}\n"
        f"SECRET_KEY={chave}\n"
    )
    DESTINO.write_text(conteudo, encoding="utf-8")
    DESTINO.chmod(0o600)
    print(f"Escrevi {DESTINO} com permissão 600. A senha não foi gravada, só o hash.")
    print("Não faça source .env: o shell trata o $ do hash bcrypt como variável.")
    print("Suba com `python app.py` ou deixe o systemd ler o arquivo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
