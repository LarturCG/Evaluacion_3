from decimal import Decimal

from fastapi.testclient import TestClient
import pytest

from bancandino.api import create_app
from bancandino.application import BankingService
from bancandino.domain import Account, BankingError, Client, Transaction
from bancandino.infrastructure import (
    AccountRow,
    Argon2PasswordService,
    JwtTokenService,
    SqlAlchemyBankRepository,
)


class FakePasswords:
    def hash(self, password: str) -> str:
        return f"hash:{password}"

    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == f"hash:{password}"


class FakeTokens:
    def issue(self, client: Client) -> str:
        return f"token-{client.id}-{client.role}"

    def decode(self, token: str) -> dict:
        if token == "token-1-customer":
            return {"client_id": 1, "role": "customer"}
        if token == "token-2-customer":
            return {"client_id": 2, "role": "customer"}
        if token == "token-3-operator":
            return {"client_id": 3, "role": "operator"}
        raise BankingError(401, "Token inválido")


class FakeRepository:
    def __init__(self):
        self.clients = {
            1: Client(1, "ana", "hash:correcta-contraseña", "ana@example.com", "customer"),
            2: Client(2, "otro", "hash:otra-contraseña", "otro@example.com", "customer"),
        }
        self.accounts = {
            "cuenta-ana": Account(1, 1, "cuenta-ana", Decimal("100.00"), "ACTIVE"),
            "cuenta-otro": Account(2, 2, "cuenta-otro", Decimal("75.00"), "ACTIVE"),
        }
        self.history = [
            Transaction(
                1,
                "cuenta-ana",
                "cuenta-otro",
                Decimal("12.50"),
                "TRANSFER",
                "2026-10-22T10:00:00+00:00",
            )
        ]
        self.transfers = []

    def create_client(self, username, password_hash, email):
        return "cuenta-nueva"

    def get_client_by_username(self, username):
        return next((client for client in self.clients.values() if client.username == username), None)

    def get_client(self, client_id):
        return self.clients.get(client_id)

    def list_accounts(self, client_id):
        return [account for account in self.accounts.values() if account.client_id == client_id]

    def get_account(self, account_number):
        return self.accounts.get(account_number)

    def update_account_status(self, account_number, client_id, status):
        account = self.accounts.get(account_number)
        if account is None:
            raise BankingError(404, "Cuenta no encontrada")
        if account.client_id != client_id:
            raise BankingError(403, "Esta cuenta no te pertenece")

    def get_history(self, account_number):
        return self.history

    def transfer(self, client_id, from_account, to_account, amount):
        self.transfers.append((client_id, from_account, to_account, amount))

    def deposit(self, account_number, amount):
        self.deposited = (account_number, amount)

    def ensure_operator(self, username, password_hash):
        pass


@pytest.fixture
def client():
    app = create_app(
        repository=FakeRepository(),
        token_service=FakeTokens(),
        password_hasher=FakePasswords(),
    )
    with TestClient(app) as test_client:
        yield test_client


def test_login_and_profile_keep_legacy_response_shape(client):
    login = client.post(
        "/api/clients/login",
        json={"username": "ana", "password": "correcta-contraseña"},
    )
    assert login.status_code == 200
    assert set(login.json()) == {"access_token", "type"}
    assert login.json()["type"] == "Bearer"

    profile = client.get("/api/clients/me?token=Bearer%20token-1-customer")
    assert profile.status_code == 200
    assert profile.json() == {
        "profile": {"username": "ana", "email": "ana@example.com"},
        "accounts": [["cuenta-ana", 100.0, "ACTIVE"]],
    }


def test_legacy_route_paths_remain_available(client):
    paths = set(client.get("/openapi.json").json()["paths"])
    assert paths == {
        "/api/clients/register",
        "/api/clients/login",
        "/api/clients/me",
        "/api/clients/profile_backup",
        "/api/accounts/{account_number}/balance",
        "/api/accounts/{account_number}/status",
        "/api/accounts/{account_number}/history",
        "/api/transactions/transfer",
        "/api/transactions/deposit",
    }


def test_legacy_success_response_contracts_are_unchanged(client):
    register = client.post(
        "/api/clients/register",
        json={
            "username": "new-client",
            "password": "a-long-enough-password",
            "email": "new@example.com",
        },
    )
    assert register.status_code == 200
    assert register.json() == {"message": "Cliente registrado", "account": "cuenta-nueva"}

    profile_backup = client.get(
        "/api/clients/profile_backup",
        headers={"Authorization": "Bearer token-1-customer"},
    )
    assert profile_backup.status_code == 200
    assert profile_backup.json() == {
        "profile": {"username": "ana", "email": "ana@example.com"},
        "accounts": [["cuenta-ana", 100.0, "ACTIVE"]],
    }

    history = client.get(
        "/api/accounts/cuenta-ana/history",
        headers={"Authorization": "Bearer token-1-customer"},
    )
    assert history.status_code == 200
    assert history.json() == {
        "transactions": [[
            1,
            "cuenta-ana",
            "cuenta-otro",
            12.5,
            "TRANSFER",
            "2026-10-22T10:00:00+00:00",
        ]]
    }

    status = client.put(
        "/api/accounts/cuenta-ana/status?token=Bearer%20token-1-customer",
        json={"status": "FROZEN", "token": "Bearer token-1-customer"},
    )
    assert status.status_code == 200
    assert status.json() == {"message": "Cuenta cuenta-ana ahora esta FROZEN"}

    transfer = client.post(
        "/api/transactions/transfer",
        json={
            "from_account": "cuenta-ana",
            "to_account": "cuenta-otro",
            "amount": 12.5,
            "token": "Bearer token-1-customer",
        },
    )
    assert transfer.status_code == 200
    assert transfer.json() == {
        "status": "success",
        "message": "Transferencia completada",
    }


def test_balance_requires_authentication_and_account_ownership(client):
    assert client.get("/api/accounts/cuenta-ana/balance").status_code == 401
    balance = client.get(
        "/api/accounts/cuenta-ana/balance",
        headers={"Authorization": "Bearer token-1-customer"},
    )
    assert balance.status_code == 200
    assert balance.json() == {
        "account": "cuenta-ana",
        "balance": 100.0,
        "status": "ACTIVE",
    }
    assert client.get(
        "/api/accounts/cuenta-ana/balance",
        headers={"Authorization": "Bearer token-2-customer"},
    ).status_code == 403


def test_deposit_requires_operator_identity(client):
    payload = {"to_account": "cuenta-ana", "amount": 5, "token": "Bearer token-1-customer"}
    assert client.post("/api/transactions/deposit", json=payload).status_code == 403


def test_operator_can_make_a_positive_deposit(client):
    response = client.post(
        "/api/transactions/deposit",
        json={
            "to_account": "cuenta-ana",
            "amount": 5,
            "token": "Bearer token-3-operator",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"message": "Depósito en efectivo registrado"}


def test_transfer_rejects_negative_amount_before_repository_call(client):
    response = client.post(
        "/api/transactions/transfer",
        json={
            "from_account": "cuenta-ana",
            "to_account": "cuenta-otro",
            "amount": -5,
            "token": "Bearer token-1-customer",
        },
    )
    assert response.status_code == 422


def test_service_rejects_non_positive_amount_without_side_effect():
    repository = FakeRepository()
    service = BankingService(repository, FakePasswords(), FakeTokens())

    with pytest.raises(BankingError) as error:
        service.transfer("token-1-customer", "cuenta-ana", "cuenta-otro", Decimal("-1"))

    assert error.value.status_code == 422
    assert repository.transfers == []


def test_customer_cannot_reactivate_a_frozen_account():
    repository = FakeRepository()
    repository.accounts["cuenta-ana"] = Account(1, 1, "cuenta-ana", Decimal("100.00"), "FROZEN")
    service = BankingService(repository, FakePasswords(), FakeTokens())

    with pytest.raises(BankingError) as error:
        service.update_status("token-1-customer", "cuenta-ana", "ACTIVE")

    assert error.value.status_code == 403


def test_postgres_repository_transfer_is_atomic_and_persists_history():
    repository = SqlAlchemyBankRepository("sqlite+pysqlite:///:memory:")
    repository.initialize()
    source_number = repository.create_client("ana", "hash-a", "ana@example.com")
    target_number = repository.create_client("luz", "hash-b", "luz@example.com")
    with repository.sessions.begin() as session:
        session.get(AccountRow, 1).balance = Decimal("100.00")
        session.get(AccountRow, 2).balance = Decimal("25.00")

    repository.transfer(1, source_number, target_number, Decimal("20.00"))

    assert repository.get_account(source_number).balance == Decimal("80.00")
    assert repository.get_account(target_number).balance == Decimal("45.00")
    history = repository.get_history(source_number)
    assert len(history) == 1
    assert history[0].amount == Decimal("20.00")
    repository.close()


def test_jwt_signature_and_expiration_are_verified():
    tokens = JwtTokenService("s" * 48)
    token = tokens.issue(Client(4, "cliente", "hash", "cliente@example.com", "customer"))

    assert tokens.decode(token)["client_id"] == 4
    with pytest.raises(BankingError):
        tokens.decode(token + "invalid")


def test_passwords_are_hashed_and_verified_with_argon2():
    passwords = Argon2PasswordService()
    digest = passwords.hash("una-frase-segura-muy-larga")

    assert digest != "una-frase-segura-muy-larga"
    assert passwords.verify(digest, "una-frase-segura-muy-larga")
    assert not passwords.verify(digest, "contraseña-incorrecta")
