import logging
import os

from app import POLITICA_CSP
from tests.credenciais import SENHA, USUARIO


def token_de(html: str) -> str:
    marcador = 'name="csrf_token" value="'
    inicio = html.find(marcador)
    assert inicio != -1
    inicio += len(marcador)
    fim = html.find('"', inicio)
    return html[inicio:fim]


def postar_login(client, usuario, senha, ip="203.0.113.10", pagina=None, extra_headers=None):
    cabecalhos = {"X-Real-IP": ip}
    if extra_headers:
        cabecalhos.update(extra_headers)
    if pagina is None:
        pagina = client.get("/login", headers=cabecalhos)
    token = token_de(pagina.get_data(as_text=True))
    return client.post(
        "/login",
        data={"username": usuario, "password": senha, "csrf_token": token},
        headers=cabecalhos,
        follow_redirects=False,
    )


def test_saude_responde_ok(client):
    resposta = client.get("/saude")
    assert resposta.status_code == 200
    assert resposta.get_data(as_text=True) == "ok\n"


def test_interno_sem_sessao_volta_ao_login(client):
    resposta = client.get("/interno")
    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]
    assert "Área interna" not in resposta.get_data(as_text=True)


def test_login_pagina_interna_e_logout(client):
    resposta = postar_login(client, USUARIO, SENHA)
    assert resposta.status_code == 303
    assert "/interno" in resposta.headers["Location"]

    pagina = client.get("/interno")
    assert pagina.status_code == 200
    texto = pagina.get_data(as_text=True)
    assert "Área interna" in texto
    assert USUARIO in texto

    token = token_de(texto)
    saida = client.post("/sair", data={"csrf_token": token}, follow_redirects=False)
    assert saida.status_code == 303
    assert "/login" in saida.headers["Location"]

    de_novo = client.get("/interno")
    assert de_novo.status_code == 302


def test_cookie_http_only_e_samesite(client):
    resposta = client.get("/login")
    cookie = resposta.headers["Set-Cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert "Secure" not in cookie.split(";", 1)[1]


def test_cookie_secure_quando_o_proxy_diz_https(client):
    resposta = client.get("/login", headers={"X-Forwarded-Proto": "https"})
    assert "Secure" in resposta.headers["Set-Cookie"]


def test_sessao_muda_depois_do_login(client):
    client.get("/login", headers={"X-Real-IP": "203.0.113.11"})
    antes = client.get_cookie("sid").value
    postar_login(client, USUARIO, SENHA, ip="203.0.113.11")
    depois = client.get_cookie("sid").value
    assert antes != depois


def test_cookie_antigo_nao_entra_depois_do_logout(client):
    postar_login(client, USUARIO, SENHA, ip="203.0.113.12")
    antiga = client.get_cookie("sid").value
    pagina = client.get("/interno")
    token = token_de(pagina.get_data(as_text=True))
    client.post("/sair", data={"csrf_token": token})
    client.set_cookie("sid", antiga)
    resposta = client.get("/interno")
    assert resposta.status_code == 302


def test_senha_errada_nao_abre_a_interna(client):
    resposta = postar_login(client, USUARIO, "senha-que-nao-e", ip="203.0.113.13")
    assert resposta.status_code == 403
    assert "Usuário ou senha inválidos." in resposta.get_data(as_text=True)
    assert client.get("/interno").status_code == 302


def test_usuario_inexistente_tem_a_mesma_mensagem(client):
    resposta = postar_login(client, "outra-pessoa", SENHA, ip="203.0.113.14")
    assert resposta.status_code == 403
    assert "Usuário ou senha inválidos." in resposta.get_data(as_text=True)


def test_nao_reflete_html_do_login(client):
    carga = "<script>alert(1)</script>"
    resposta = postar_login(client, carga, "qualquer", ip="203.0.113.15")
    corpo = resposta.get_data(as_text=True)
    assert "<script>" not in corpo
    assert carga not in corpo


def test_template_interno_escapa_html(aplicacao):
    with aplicacao.test_request_context():
        html = aplicacao.jinja_env.get_template("interno.html").render(
            usuario="<script>alert(1)</script>",
            emitida_em="01/01/2026 00:00 UTC",
            csrf_token="abc",
        )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_csrf_ausente_e_recusado(client):
    client.get("/login", headers={"X-Real-IP": "203.0.113.16"})
    resposta = client.post(
        "/login",
        data={"username": USUARIO, "password": SENHA, "csrf_token": "nao-confere"},
        headers={"X-Real-IP": "203.0.113.16"},
    )
    assert resposta.status_code == 400
    assert client.get("/interno").status_code == 302


def test_bloqueio_depois_de_cinco_falhas(client):
    ip = "203.0.113.17"
    ultima = None
    for _ in range(5):
        ultima = postar_login(client, USUARIO, "errada", ip=ip)
        assert ultima.status_code == 403
    bloqueada = postar_login(client, USUARIO, SENHA, ip=ip)
    assert bloqueada.status_code == 429
    assert "Muitas tentativas" in bloqueada.get_data(as_text=True)

    outro_ip = postar_login(client, USUARIO, SENHA, ip="203.0.113.18")
    assert outro_ip.status_code == 303


def test_get_no_logout_nao_encerra(client):
    postar_login(client, USUARIO, SENHA, ip="203.0.113.19")
    resposta = client.get("/sair")
    assert resposta.status_code == 405
    assert client.get("/interno").status_code == 200


def test_logout_sem_csrf_mantem_a_sessao(client):
    postar_login(client, USUARIO, SENHA, ip="203.0.113.21")
    resposta = client.post("/sair", data={"csrf_token": "errado"})
    assert resposta.status_code == 400
    assert client.get("/interno").status_code == 200


def test_cabecalhos_de_seguranca(client):
    resposta = client.get("/login")
    assert resposta.headers["Content-Security-Policy"] == POLITICA_CSP
    assert resposta.headers["X-Content-Type-Options"] == "nosniff"
    assert resposta.headers["X-Frame-Options"] == "DENY"
    assert resposta.headers["Referrer-Policy"] == "no-referrer"
    assert "script-src 'none'" in resposta.headers["Content-Security-Policy"]
    assert resposta.headers["Cache-Control"] == "no-store"


def test_404_nao_mostra_rastreio(client):
    resposta = client.get("/nao-existe")
    corpo = resposta.get_data(as_text=True)
    assert resposta.status_code == 404
    assert "Traceback" not in corpo
    assert "Página não encontrada." in corpo
    assert resposta.headers["Content-Security-Policy"] == POLITICA_CSP


def test_erro_interno_nao_devolve_o_detalhe(aplicacao):
    @aplicacao.route("/_quebra")
    def quebra():
        raise RuntimeError("detalhe-secreto-do-servidor")

    cliente = aplicacao.test_client()
    resposta = cliente.get("/_quebra")
    corpo = resposta.get_data(as_text=True)
    assert resposta.status_code == 500
    assert "detalhe-secreto-do-servidor" not in corpo
    assert "Traceback" not in corpo
    assert "Não foi possível concluir a operação." in corpo


def test_log_de_falha_nao_guarda_a_senha(client, caplog):
    with caplog.at_level(logging.WARNING, logger="app"):
        postar_login(client, USUARIO, "segredo-que-nao-pode-aparecer", ip="203.0.113.22")
    assert "segredo-que-nao-pode-aparecer" not in caplog.text
    assert "login_falha" in caplog.text


def test_aplicacao_nao_esta_em_debug(aplicacao):
    assert aplicacao.debug is False
    assert aplicacao.config["PROPAGATE_EXCEPTIONS"] is False


def test_env_ilegivel_nao_interrompe(tmp_path, monkeypatch):
    from app import carregar_env

    arquivo = tmp_path / ".env"
    arquivo.write_text("NAO_DEVE_ENTRAR=1\n", encoding="utf-8")
    arquivo.chmod(0)
    if os.access(arquivo, os.R_OK):
        # root ignora o mode 0. O caso da VM é um processo sem esse privilégio.
        return
    monkeypatch.delenv("NAO_DEVE_ENTRAR", raising=False)
    carregar_env(arquivo)
    assert os.environ.get("NAO_DEVE_ENTRAR") is None
