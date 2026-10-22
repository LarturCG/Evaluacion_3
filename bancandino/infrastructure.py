from datetime import datetime, timedelta, timezone
from decimal import Decimal

from argon2 import PasswordHasher as Argon2Hasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import ForeignKey, Integer, Numeric, String, create_engine, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import jwt
from bancandino.domain import Account, BankingError, Client, Transaction


class Base(DeclarativeBase):
    pass


class ClientRow(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="customer")


class AccountRow(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    account_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")


class TransactionRow(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_account: Mapped[str] = mapped_column(String(32), nullable=False)
    to_account: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    date: Mapped[str] = mapped_column(String(40), nullable=False)


class Argon2PasswordService:
    def __init__(self) -> None:
        self.hasher = Argon2Hasher()

    def hash(self, password: str) -> str:
        return self.hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self.hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False


class JwtTokenService:
    def __init__(self, secret: str, algorithm: str = "HS256"):
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("JWT_SECRET debe contener al menos 32 bytes")
        self.secret = secret
        self.algorithm = algorithm

    def issue(self, client: Client) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": client.username,
            "client_id": client.id,
            "role": client.role,
            "iat": now,
            "exp": now.replace(microsecond=0) + timedelta(hours=1),
        }
        return jwt.encode(payload, self.secret, algorithm=self.algorithm)

    def decode(self, token: str) -> dict:
        try:
            payload = jwt.decode(token, self.secret, algorithms=[self.algorithm])
            if not isinstance(payload.get("client_id"), int) or not isinstance(payload.get("sub"), str):
                raise jwt.InvalidTokenError("Invalid claims")
            return payload
        except jwt.InvalidTokenError as error:
            raise BankingError(401, "Token inválido") from error


class SqlAlchemyBankRepository:
    def __init__(self, database_url, connect_args: dict | None = None):
        self.engine = create_engine(database_url, pool_pre_ping=True, connect_args=connect_args or {})
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)

    def close(self) -> None:
        self.engine.dispose()

    def create_client(self, username: str, password_hash: str, email: str) -> str:
        try:
            with self.sessions.begin() as session:
                client = ClientRow(username=username, password_hash=password_hash, email=email, role="customer")
                session.add(client)
                session.flush()
                account_number = f"1000-2000-900{client.id}"
                session.add(AccountRow(client_id=client.id, account_number=account_number))
            return account_number
        except IntegrityError as error:
            raise ValueError("Usuario ya existe") from error

    def get_client_by_username(self, username: str) -> Client | None:
        with self.sessions() as session:
            row = session.scalar(select(ClientRow).where(ClientRow.username == username))
            return self._client(row) if row else None

    def get_client(self, client_id: int) -> Client | None:
        with self.sessions() as session:
            row = session.get(ClientRow, client_id)
            return self._client(row) if row else None

    def list_accounts(self, client_id: int) -> list[Account]:
        with self.sessions() as session:
            rows = session.scalars(select(AccountRow).where(AccountRow.client_id == client_id).order_by(AccountRow.id))
            return [self._account(row) for row in rows]

    def get_account(self, account_number: str) -> Account | None:
        with self.sessions() as session:
            row = session.scalar(select(AccountRow).where(AccountRow.account_number == account_number))
            return self._account(row) if row else None

    def update_account_status(self, account_number: str, client_id: int, status: str) -> None:
        with self.sessions.begin() as session:
            row = session.scalar(select(AccountRow).where(AccountRow.account_number == account_number).with_for_update())
            if row is None:
                raise BankingError(404, "Cuenta no encontrada")
            if row.client_id != client_id:
                raise BankingError(403, "Esta cuenta no te pertenece")
            row.status = status

    def get_history(self, account_number: str) -> list[Transaction]:
        with self.sessions() as session:
            rows = session.scalars(
                select(TransactionRow)
                .where(or_(TransactionRow.from_account == account_number, TransactionRow.to_account == account_number))
                .order_by(TransactionRow.id)
            )
            return [self._transaction(row) for row in rows]

    def transfer(self, client_id: int, from_account: str, to_account: str, amount: Decimal) -> None:
        with self.sessions.begin() as session:
            rows = session.scalars(
                select(AccountRow)
                .where(AccountRow.account_number.in_([from_account, to_account]))
                .order_by(AccountRow.account_number)
                .with_for_update()
            ).all()
            accounts = {row.account_number: row for row in rows}
            source = accounts.get(from_account)
            target = accounts.get(to_account)
            if source is None:
                raise BankingError(404, "Cuenta origen no existe")
            if source.client_id != client_id:
                raise BankingError(403, "Esta cuenta no te pertenece")
            if source.status != "ACTIVE":
                raise BankingError(400, "Cuenta origen congelada")
            if target is None:
                raise BankingError(404, "Cuenta destino no existe")
            if target.status != "ACTIVE":
                raise BankingError(400, "Cuenta destino no puede recibir fondos")
            if source.balance < amount:
                raise BankingError(400, "Saldo insuficiente")
            source.balance -= amount
            target.balance += amount
            session.add(TransactionRow(
                from_account=from_account,
                to_account=to_account,
                amount=amount,
                type="TRANSFER",
                date=datetime.now(timezone.utc).isoformat(),
            ))

    def deposit(self, account_number: str, amount: Decimal) -> None:
        with self.sessions.begin() as session:
            account = session.scalar(
                select(AccountRow).where(AccountRow.account_number == account_number).with_for_update()
            )
            if account is None:
                raise BankingError(404, "Cuenta destino no existe")
            if account.status != "ACTIVE":
                raise BankingError(400, "Cuenta destino no puede recibir fondos")
            account.balance += amount
            session.add(TransactionRow(
                from_account="CASH",
                to_account=account_number,
                amount=amount,
                type="DEPOSIT",
                date=datetime.now(timezone.utc).isoformat(),
            ))

    def ensure_operator(self, username: str, password_hash: str) -> None:
        with self.sessions.begin() as session:
            existing = session.scalar(select(ClientRow).where(ClientRow.username == username))
            if existing is None:
                session.add(ClientRow(
                    username=username,
                    password_hash=password_hash,
                    email=f"{username}@localhost",
                    role="operator",
                ))

    @staticmethod
    def _client(row: ClientRow) -> Client:
        return Client(row.id, row.username, row.password_hash, row.email, row.role)

    @staticmethod
    def _account(row: AccountRow) -> Account:
        return Account(row.id, row.client_id, row.account_number, row.balance, row.status)

    @staticmethod
    def _transaction(row: TransactionRow) -> Transaction:
        return Transaction(row.id, row.from_account, row.to_account, row.amount, row.type, row.date)
