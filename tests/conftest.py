import os

import bcrypt

from tests.credenciais import SENHA, USUARIO

os.environ["DEMO_USERNAME"] = USUARIO
os.environ["DEMO_PASSWORD_HASH"] = bcrypt.hashpw(
    SENHA.encode("utf-8"), bcrypt.gensalt(rounds=4)
).decode("ascii")
os.environ["SECRET_KEY"] = "teste-somente-local-nao-e-segredo-de-producao-32"


import pytest

from app import criar_app


@pytest.fixture
def aplicacao():
    criada = criar_app()
    criada.config["PROPAGATE_EXCEPTIONS"] = False
    return criada


@pytest.fixture
def client(aplicacao):
    return aplicacao.test_client()
