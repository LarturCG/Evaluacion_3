import re
from decimal import Decimal, InvalidOperation
from typing import Protocol

from bancandino.domain import Account, BankRepository, BankingError, Client, Transaction


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...
    def verify(self, password_hash: str, password: str) -> bool: ...


class TokenService(Protocol):
    def issue(self, client: Client) -> str: ...
    def decode(self, token: str) -> dict: ...


class BankingService:
    def __init__(self, repository: BankRepository, passwords: PasswordHasher, tokens: TokenService):
        self.repository = repository
        self.passwords = passwords
        self.tokens = tokens

    def register(self, username: str, password: str, email: str) -> str:
        if not username.strip() or len(username) > 80:
            raise BankingError(422, "Nombre de usuario inválido")
        if len(password) < 12 or len(password) > 128:
            raise BankingError(422, "La contraseña debe tener entre 12 y 128 caracteres")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise BankingError(422, "Correo electrónico inválido")
        try:
            return self.repository.create_client(username, self.passwords.hash(password), email)
        except ValueError as error:
            raise BankingError(400, str(error)) from error

    def login(self, username: str, password: str) -> str:
        client = self.repository.get_client_by_username(username)
        if client is None or not self.passwords.verify(client.password_hash, password):
            raise BankingError(401, "Credenciales incorrectas")
        return self.tokens.issue(client)

    def profile(self, token: str) -> tuple[Client, list[Account]]:
        identity = self.tokens.decode(token)
        client = self.repository.get_client(identity["client_id"])
        if client is None:
            raise BankingError(401, "Token inválido")
        return client, list(self.repository.list_accounts(client.id))

    def balance(self, token: str, account_number: str) -> Account:
        identity = self.tokens.decode(token)
        account = self._owned_account(account_number, identity)
        return account

    def history(self, token: str, account_number: str) -> list[Transaction]:
        identity = self.tokens.decode(token)
        self._owned_account(account_number, identity)
        return list(self.repository.get_history(account_number))

    def update_status(self, token: str, account_number: str, status: str) -> None:
        identity = self.tokens.decode(token)
        if status not in {"ACTIVE", "FROZEN"}:
            raise BankingError(422, "Estado de cuenta inválido")
        account = self._owned_account(account_number, identity)
        if account.status == "FROZEN" and status == "ACTIVE" and identity.get("role") != "operator":
            raise BankingError(403, "Solo un operador puede reactivar una cuenta congelada")
        self.repository.update_account_status(account_number, account.client_id, status)

    def transfer(self, token: str, from_account: str, to_account: str, amount: Decimal) -> None:
        identity = self.tokens.decode(token)
        value = self._positive_amount(amount)
        if from_account == to_account:
            raise BankingError(400, "Las cuentas de origen y destino deben ser distintas")
        self.repository.transfer(identity["client_id"], from_account, to_account, value)

    def deposit(self, token: str, account_number: str, amount: Decimal) -> None:
        identity = self.tokens.decode(token)
        if identity.get("role") != "operator":
            raise BankingError(403, "Se requiere un operador bancario")
        self.repository.deposit(account_number, self._positive_amount(amount))

    def create_operator(self, username: str, password: str) -> None:
        if len(password) < 16:
            raise ValueError("BANK_OPERATOR_PASSWORD debe tener al menos 16 caracteres")
        self.repository.ensure_operator(username, self.passwords.hash(password))

    def _owned_account(self, account_number: str, identity: dict) -> Account:
        account = self.repository.get_account(account_number)
        if account is None:
            raise BankingError(404, "Cuenta no encontrada")
        if account.client_id != identity["client_id"] and identity.get("role") != "operator":
            raise BankingError(403, "Esta cuenta no te pertenece")
        return account

    @staticmethod
    def _positive_amount(amount: Decimal) -> Decimal:
        try:
            value = Decimal(str(amount))
        except (InvalidOperation, ValueError) as error:
            raise BankingError(422, "Monto inválido") from error
        if not value.is_finite() or value <= 0 or value.as_tuple().exponent < -2:
            raise BankingError(422, "El monto debe ser positivo y tener máximo dos decimales")
        return value
