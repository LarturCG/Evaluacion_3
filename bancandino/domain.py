from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, Sequence


@dataclass(frozen=True)
class Client:
    id: int
    username: str
    password_hash: str
    email: str
    role: str


@dataclass(frozen=True)
class Account:
    id: int
    client_id: int
    account_number: str
    balance: Decimal
    status: str


@dataclass(frozen=True)
class Transaction:
    id: int
    from_account: str
    to_account: str
    amount: Decimal
    type: str
    date: str


class BankingError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class BankRepository(Protocol):
    def create_client(self, username: str, password_hash: str, email: str) -> str: ...
    def get_client_by_username(self, username: str) -> Client | None: ...
    def get_client(self, client_id: int) -> Client | None: ...
    def list_accounts(self, client_id: int) -> Sequence[Account]: ...
    def get_account(self, account_number: str) -> Account | None: ...
    def update_account_status(self, account_number: str, client_id: int, status: str) -> None: ...
    def get_history(self, account_number: str) -> Sequence[Transaction]: ...
    def transfer(self, client_id: int, from_account: str, to_account: str, amount: Decimal) -> None: ...
    def deposit(self, account_number: str, amount: Decimal) -> None: ...
    def ensure_operator(self, username: str, password_hash: str) -> None: ...
