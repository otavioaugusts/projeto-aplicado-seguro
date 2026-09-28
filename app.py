"""Protótipo web do Projeto Aplicado (UNCISAL).

A sessão mora no servidor. O navegador só guarda um identificador opaco
no cookie `sid`. Sem esse identificador válido, /interno não é entregue.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import secrets
import threading
import time
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

import bcrypt
from dotenv import load_dotenv
from flask import Flask, g, make_response, redirect, render_template, request, url_for
from werkzeug.exceptions import HTTPException

# Mesmo texto no Nginx (infra/nginx/projeto-aplicado.conf). Os dois cabeçalhos
# se somam no navegador; se divergirem, a página quebra.
POLITICA_CSP = (
    "default-src 'self'; "
    "script-src 'none'; "
    "style-src 'self'; "
    "img-src 'self'; "
    "font-src 'self'; "
    "form-action 'self'; "
    "base-uri 'none'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "frame-src 'none'"
)

JANELA_FALHAS_SEGUNDOS = 15 * 60
LIMITE_FALHAS = 5
DURACAO_ANONIMA = 30 * 60
DURACAO_AUTENTICADA = 8 * 60 * 60
LIMITE_SETORES = 5000

_USUARIO_OK = re.compile(r"[A-Za-z0-9._-]{1,64}")
_IP_OK = re.compile(r"^[0-9A-Fa-f:.]{3,64}$")

MENSAGEM_CREDENCIAL = "Usuário ou senha inválidos."
MENSAGEM_BLOQUEIO = "Muitas tentativas. Tente novamente mais tarde."
MENSAGEM_FORMULARIO = "Não foi possível validar o formulário. Atualize a página e tente de novo."


class Estado:
    """Estado de autenticação deste processo. Não vai para o disco."""

    def __init__(self, usuario: str, hash_real: bytes, hash_falso: bytes) -> None:
        self.usuario = usuario
        self.hash_real = hash_real
        self.hash_falso = hash_falso
        self.sessoes: dict[str, dict] = {}
        self.falhas: dict[str, list[float]] = {}
        self.cadeado = threading.Lock()


def carregar_env() -> None:
    caminho = Path(__file__).resolve().parent / ".env"
    if caminho.is_file():
        # Não sobrescreve variável que o systemd ou o teste já definiu.
        load_dotenv(caminho, override=False)


def _exigir(nome: str) -> str:
    valor = os.environ.get(nome, "").strip()
    if not valor or valor.startswith("substitua-"):
        raise RuntimeError(
            f"{nome} não está definida. Copie .env.example para .env e rode scripts/init_env.py."
        )
    return valor


def _custo_bcrypt(hash_texto: str) -> int:
    partes = hash_texto.split("$")
    if len(partes) < 4 or partes[1] not in {"2a", "2b", "2y"}:
        raise RuntimeError("DEMO_PASSWORD_HASH precisa ser um hash bcrypt, no formato $2b$...")
    try:
        custo = int(partes[2])
    except ValueError as erro:
        raise RuntimeError("O custo do hash bcrypt é inválido.") from erro
    if not 4 <= custo <= 16:
        raise RuntimeError("O custo do hash bcrypt está fora da faixa aceita (4 a 16).")
    return custo


def texto_log(valor: str) -> str:
    """Tira quebra de linha para um campo de log não forjar outra entrada."""
    return valor.replace("\r", "").replace("\n", "")[:80]


def ip_do_cliente() -> str:
    """Usa o IP que o Nginx gravou em X-Real-IP.

    O Gunicorn só escuta em 127.0.0.1, e o Nginx substitui esse cabeçalho
    pelo IP da conexão real. Fora do localhost o cabeçalho é ignorado.
    """
    remoto = request.remote_addr or ""
    if remoto in {"127.0.0.1", "::1"}:
        informado = request.headers.get("X-Real-IP", "").strip()
        if informado and _IP_OK.fullmatch(informado):
            return informado
    if remoto and _IP_OK.fullmatch(remoto):
        return remoto
    return "desconhecido"


def conexao_https() -> bool:
    if request.is_secure:
        return True
    if request.remote_addr in {"127.0.0.1", "::1"}:
        proto = request.headers.get("X-Forwarded-Proto", "")
        return proto.split(",")[0].strip().lower() == "https"
    return False


def origem_aceita() -> bool:
    origem = request.headers.get("Origin")
    if not origem:
        return True
    partes = urlsplit(origem)
    if partes.scheme not in {"http", "https"} or not partes.netloc:
        return False
    return partes.netloc == request.host


def _podar_falhas(estado: Estado, ip: str, agora: float) -> list[float]:
    marcas = [t for t in estado.falhas.get(ip, []) if agora - t < JANELA_FALHAS_SEGUNDOS]
    if marcas:
        estado.falhas[ip] = marcas
    else:
        estado.falhas.pop(ip, None)
    return marcas


def bloqueado(estado: Estado, ip: str) -> bool:
    agora = time.time()
    with estado.cadeado:
        return len(_podar_falhas(estado, ip, agora)) >= LIMITE_FALHAS


def registrar_falha(estado: Estado, ip: str) -> None:
    agora = time.time()
    with estado.cadeado:
        marcas = _podar_falhas(estado, ip, agora)
        marcas.append(agora)
        estado.falhas[ip] = marcas
        while len(estado.falhas) > LIMITE_SETORES:
            estado.falhas.pop(next(iter(estado.falhas)))


def limpar_falhas(estado: Estado, ip: str) -> None:
    with estado.cadeado:
        estado.falhas.pop(ip, None)


def criar_sessao(estado: Estado, autenticada: bool, usuario: str, duracao: int) -> tuple[str, str]:
    identificador = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    agora = time.time()
    registro = {
        "autenticada": autenticada,
        "usuario": usuario,
        "csrf": csrf,
        "expira": agora + duracao,
        "emitida_em": time.strftime("%d/%m/%Y %H:%M UTC", time.gmtime(agora)),
        "duracao": duracao,
    }
    with estado.cadeado:
        while len(estado.sessoes) >= LIMITE_SETORES:
            estado.sessoes.pop(next(iter(estado.sessoes)))
        estado.sessoes[identificador] = registro
    return identificador, csrf


def obter_sessao(estado: Estado) -> tuple[dict, str] | None:
    identificador = request.cookies.get("sid", "")
    if not identificador:
        return None
    with estado.cadeado:
        registro = estado.sessoes.get(identificador)
        if registro is None:
            return None
        if registro["expira"] <= time.time():
            estado.sessoes.pop(identificador, None)
            return None
        return dict(registro), identificador


def apagar_sessao(estado: Estado, identificador: str) -> None:
    with estado.cadeado:
        estado.sessoes.pop(identificador, None)


def credencial_aceita(estado: Estado, usuario: str, senha: str) -> bool:
    senha_bytes = senha.encode("utf-8")
    usuario_ruim = (
        not usuario
        or len(usuario) > 64
        or "\n" in usuario
        or "\r" in usuario
        or not senha_bytes
        or len(senha_bytes) > 72
    )
    if usuario_ruim:
        try:
            bcrypt.checkpw(b"tamanho-invalido", estado.hash_falso)
        except ValueError:
            pass
        return False
    usuario_ok = hmac.compare_digest(usuario, estado.usuario)
    alvo = estado.hash_real if usuario_ok else estado.hash_falso
    try:
        senha_ok = bcrypt.checkpw(senha_bytes, alvo)
    except ValueError:
        senha_ok = False
    return usuario_ok and senha_ok


def gravar_cookie(resposta, identificador: str, duracao: int) -> None:
    resposta.set_cookie(
        "sid",
        identificador,
        max_age=duracao,
        httponly=True,
        secure=conexao_https(),
        samesite="Strict",
        path="/",
    )


def limpar_cookie(resposta) -> None:
    resposta.set_cookie(
        "sid",
        "",
        max_age=0,
        httponly=True,
        secure=conexao_https(),
        samesite="Strict",
        path="/",
    )


def csrf_confere(sessao: dict, enviado: str) -> bool:
    guardado = sessao.get("csrf", "")
    if not guardado or not enviado:
        return False
    return hmac.compare_digest(guardado, enviado)


def criar_app() -> Flask:
    usuario = _exigir("DEMO_USERNAME")
    if not _USUARIO_OK.fullmatch(usuario):
        raise RuntimeError("DEMO_USERNAME só pode ter letras, números, ponto, _ e -.")
    hash_texto = _exigir("DEMO_PASSWORD_HASH")
    segredo = _exigir("SECRET_KEY")
    if len(segredo) < 32:
        raise RuntimeError("SECRET_KEY precisa ter pelo menos 32 caracteres.")
    custo = _custo_bcrypt(hash_texto)

    aplicacao = Flask(__name__)
    aplicacao.config["SECRET_KEY"] = segredo
    aplicacao.config["DEBUG"] = False
    aplicacao.config["PROPAGATE_EXCEPTIONS"] = False
    aplicacao.config["MAX_CONTENT_LENGTH"] = 16 * 1024
    aplicacao.config["SESSION_COOKIE_HTTPONLY"] = True
    aplicacao.config["SESSION_COOKIE_SAMESITE"] = "Strict"

    hash_falso = bcrypt.hashpw(secrets.token_bytes(32), bcrypt.gensalt(rounds=custo))
    aplicacao.extensions["estado"] = Estado(usuario, hash_texto.encode("utf-8"), hash_falso)
    if custo < 12:
        aplicacao.logger.warning(
            "hash bcrypt com custo %s; em produção o scripts/init_env.py usa o custo padrão (12)",
            custo,
        )

    @aplicacao.after_request
    def adicionar_cabecalhos(resposta):
        resposta.headers["Content-Security-Policy"] = POLITICA_CSP
        resposta.headers["X-Content-Type-Options"] = "nosniff"
        resposta.headers["X-Frame-Options"] = "DENY"
        resposta.headers["Referrer-Policy"] = "no-referrer"
        resposta.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        resposta.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        resposta.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        resposta.headers["X-Robots-Tag"] = "noindex"
        resposta.headers["Cache-Control"] = "no-store"
        if conexao_https():
            resposta.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        resposta.headers.pop("Server", None)
        return resposta

    @aplicacao.get("/saude")
    def saude():
        return "ok\n", 200, {"Content-Type": "text/plain; charset=utf-8"}

    @aplicacao.get("/")
    def raiz():
        estado = aplicacao.extensions["estado"]
        achou = obter_sessao(estado)
        if achou and achou[0]["autenticada"]:
            return redirect(url_for("interno"))
        return redirect(url_for("login"))

    def pagina_de_login(mensagem: str | None, status: int):
        estado = aplicacao.extensions["estado"]
        identificador, csrf = criar_sessao(estado, False, "", DURACAO_ANONIMA)
        corpo = render_template("login.html", erro=mensagem, csrf_token=csrf)
        resposta = make_response(corpo, status)
        gravar_cookie(resposta, identificador, DURACAO_ANONIMA)
        if status == 429:
            resposta.headers["Retry-After"] = str(JANELA_FALHAS_SEGUNDOS)
        return resposta

    @aplicacao.get("/login")
    def login():
        estado = aplicacao.extensions["estado"]
        achou = obter_sessao(estado)
        if achou and achou[0]["autenticada"]:
            return redirect(url_for("interno"))
        return pagina_de_login(None, 200)

    @aplicacao.post("/login")
    def login_post():
        estado: Estado = aplicacao.extensions["estado"]
        ip = ip_do_cliente()
        ja_entrou = obter_sessao(estado)
        if ja_entrou and ja_entrou[0]["autenticada"]:
            return redirect(url_for("interno"))
        if bloqueado(estado, ip):
            aplicacao.logger.warning("login_bloqueio ip=%s", ip)
            return pagina_de_login(MENSAGEM_BLOQUEIO, 429)

        if not origem_aceita():
            aplicacao.logger.warning("login_origem_recusada ip=%s", ip)
            return pagina_de_login(MENSAGEM_FORMULARIO, 400)

        achou = obter_sessao(estado)
        enviado = request.form.get("csrf_token", "")
        if achou is None or not csrf_confere(achou[0], enviado):
            aplicacao.logger.warning("login_csrf_invalido ip=%s", ip)
            return pagina_de_login(MENSAGEM_FORMULARIO, 400)

        usuario = request.form.get("username", "")
        senha = request.form.get("password", "")
        if not credencial_aceita(estado, usuario, senha):
            registrar_falha(estado, ip)
            aplicacao.logger.warning("login_falha usuario=%s ip=%s", texto_log(usuario), ip)
            return pagina_de_login(MENSAGEM_CREDENCIAL, 403)

        # Troca o identificador depois da senha certa (evita fixação de sessão).
        apagar_sessao(estado, achou[1])
        limpar_falhas(estado, ip)
        identificador, _csrf = criar_sessao(estado, True, estado.usuario, DURACAO_AUTENTICADA)
        aplicacao.logger.info("login_ok usuario=%s ip=%s", texto_log(estado.usuario), ip)
        resposta = make_response(redirect(url_for("interno"), code=303))
        gravar_cookie(resposta, identificador, DURACAO_AUTENTICADA)
        return resposta

    def exigir_autenticacao(view):
        @wraps(view)
        def envelope(*args, **kwargs):
            achou = obter_sessao(aplicacao.extensions["estado"])
            if achou is None or not achou[0]["autenticada"]:
                return redirect(url_for("login"))
            g.sessao = achou[0]
            g.sid = achou[1]
            return view(*args, **kwargs)

        return envelope

    @aplicacao.get("/interno")
    @exigir_autenticacao
    def interno():
        return render_template(
            "interno.html",
            usuario=g.sessao["usuario"],
            emitida_em=g.sessao["emitida_em"],
            csrf_token=g.sessao["csrf"],
        )

    @aplicacao.post("/sair")
    @exigir_autenticacao
    def sair():
        estado = aplicacao.extensions["estado"]
        if not origem_aceita() or not csrf_confere(g.sessao, request.form.get("csrf_token", "")):
            aplicacao.logger.warning("logout_recusado ip=%s", ip_do_cliente())
            return (
                render_template(
                    "erro.html",
                    titulo="Não foi possível sair",
                    mensagem=MENSAGEM_FORMULARIO,
                ),
                400,
            )
        apagar_sessao(estado, g.sid)
        aplicacao.logger.info(
            "logout usuario=%s ip=%s",
            texto_log(g.sessao["usuario"]),
            ip_do_cliente(),
        )
        resposta = make_response(redirect(url_for("login"), code=303))
        limpar_cookie(resposta)
        return resposta

    @aplicacao.errorhandler(HTTPException)
    def erro_http(erro: HTTPException):
        mensagens = {
            400: "Não foi possível processar a requisição.",
            404: "Página não encontrada.",
            405: "Método não permitido.",
            413: "Requisição grande demais.",
            429: MENSAGEM_BLOQUEIO,
        }
        mensagem = mensagens.get(erro.code or 500, "Não foi possível concluir a operação.")
        return (
            render_template("erro.html", titulo="Não foi possível continuar", mensagem=mensagem),
            erro.code or 500,
        )

    @aplicacao.errorhandler(Exception)
    def erro_nao_tratado(erro: Exception):
        if isinstance(erro, HTTPException):
            return erro_http(erro)
        # O traceback fica no journal do serviço. A resposta não leva detalhe.
        aplicacao.logger.exception("erro_interno")
        return (
            render_template(
                "erro.html",
                titulo="Erro interno",
                mensagem="Não foi possível concluir a operação.",
            ),
            500,
        )

    aplicacao.logger.setLevel(logging.INFO)
    return aplicacao


carregar_env()
app = criar_app()


if __name__ == "__main__":
    # Só para ensaio na máquina local. Em produção quem escuta é o Gunicorn.
    app.run(host="127.0.0.1", port=8000, debug=False)
