from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
import os

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import URL

from bancandino.application import BankingService, PasswordHasher, TokenService
from bancandino.domain import BankRepository, BankingError
from bancandino.infrastructure import Argon2PasswordService, JwtTokenService, SqlAlchemyBankRepository


@dataclass(frozen=True)
class Settings:
    database_user: str
    database_password: str
    database_host: str
    database_port: int
    database_name: str
    jwt_secret: str
    operator_username: str | None = None
    operator_password: str | None = None

    @classmethod
    def from_environment(cls) -> "Settings":
        def configured_value(name: str) -> str | None:
            file_path = os.getenv(f"{name}_FILE")
            if file_path:
                try:
                    with open(file_path, encoding="utf-8") as secret_file:
                        return secret_file.read().strip()
                except OSError as error:
                    raise RuntimeError(f"No se pudo leer el secreto configurado en {file_path}") from error
            return os.getenv(name)

        values = {
            "POSTGRES_USER": configured_value("POSTGRES_USER"),
            "POSTGRES_PASSWORD": configured_value("POSTGRES_PASSWORD"),
            "POSTGRES_HOST": configured_value("POSTGRES_HOST"),
            "POSTGRES_DB": configured_value("POSTGRES_DB"),
            "JWT_SECRET": configured_value("JWT_SECRET"),
        }
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise RuntimeError(f"Faltan variables de configuración obligatorias: {', '.join(missing)}")
        secret = values["JWT_SECRET"]
        if len(secret.encode("utf-8")) < 32:
            raise RuntimeError("JWT_SECRET debe contener al menos 32 bytes")
        operator_username = os.getenv("BANK_OPERATOR_USERNAME") or None
        operator_password = configured_value("BANK_OPERATOR_PASSWORD")
        if bool(operator_username) != bool(operator_password):
            raise RuntimeError("BANK_OPERATOR_USERNAME y BANK_OPERATOR_PASSWORD deben configurarse juntos")
        return cls(
            database_user=values["POSTGRES_USER"],
            database_password=values["POSTGRES_PASSWORD"],
            database_host=values["POSTGRES_HOST"],
            database_port=int(os.getenv("POSTGRES_PORT", "5432")),
            database_name=values["POSTGRES_DB"],
            jwt_secret=secret,
            operator_username=operator_username,
            operator_password=operator_password,
        )

    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.database_user,
            password=self.database_password,
            host=self.database_host,
            port=self.database_port,
            database=self.database_name,
        )


class RegisterRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=128)
    email: str = Field(min_length=3, max_length=254)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=128)


class TransferRequest(BaseModel):
    from_account: str
    to_account: str
    amount: Decimal = Field(gt=0, allow_inf_nan=False, decimal_places=2)
    token: str


class StatusRequest(BaseModel):
    status: str
    token: str


class DepositRequest(TransferRequest):
    from_account: str = "CASH"
    token: str | None = None


def create_app(
    repository: BankRepository | None = None,
    token_service: TokenService | None = None,
    password_hasher: PasswordHasher | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    owns_repository = repository is None
    if owns_repository:
        settings = settings or Settings.from_environment()
        repository = SqlAlchemyBankRepository(settings.database_url())
    if repository is None:
        raise RuntimeError("Se requiere un repositorio bancario")
    if token_service is None:
        if settings is None:
            settings = Settings.from_environment()
        token_service = JwtTokenService(settings.jwt_secret)
    password_hasher = password_hasher or Argon2PasswordService()
    service = BankingService(repository, password_hasher, token_service)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if owns_repository:
            repository.initialize()
            if settings.operator_username and settings.operator_password:
                service.create_operator(settings.operator_username, settings.operator_password)
        try:
            yield
        finally:
            if owns_repository:
                repository.close()

    app = FastAPI(title="Bancandino Core", lifespan=lifespan)
    app.state.banking_service = service

    @app.exception_handler(BankingError)
    async def banking_error_handler(_, error: BankingError):
        return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

    def access_token(query_token: str | None, authorization: str | None) -> str:
        if authorization is not None:
            if not authorization.startswith("Bearer "):
                raise HTTPException(status_code=401, detail="Token inválido")
            return authorization[7:]
        if query_token is None or not query_token.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Token faltante")
        return query_token[7:]

    @app.post("/api/clients/register")
    def register_client(request: RegisterRequest):
        account_number = service.register(request.username, request.password, request.email)
        return {"message": "Cliente registrado", "account": account_number}

    @app.post("/api/clients/login")
    def login(request: LoginRequest):
        token = service.login(request.username, request.password)
        return {"access_token": token, "type": "Bearer"}

    @app.get("/api/clients/me")
    def get_my_profile(token: str | None = Query(None), authorization: str | None = Header(None)):
        client, accounts = service.profile(access_token(token, authorization))
        return {
            "profile": {"username": client.username, "email": client.email},
            "accounts": [(account.account_number, float(account.balance), account.status) for account in accounts],
        }

    @app.get("/api/clients/profile_backup")
    def get_profile_backup(token: str | None = Query(None), authorization: str | None = Header(None)):
        client, accounts = service.profile(access_token(token, authorization))
        return {
            "profile": {"username": client.username, "email": client.email},
            "accounts": [(account.account_number, float(account.balance), account.status) for account in accounts],
        }

    @app.get("/api/accounts/{account_number}/balance")
    def get_balance(
        account_number: str,
        token: str | None = Query(None),
        authorization: str | None = Header(None),
    ):
        account = service.balance(access_token(token, authorization), account_number)
        return {"account": account.account_number, "balance": float(account.balance), "status": account.status}

    @app.put("/api/accounts/{account_number}/status")
    def update_status(
        account_number: str,
        req: StatusRequest,
        token: str | None = Query(None),
        authorization: str | None = Header(None),
    ):
        service.update_status(access_token(token, authorization), account_number, req.status)
        return {"message": f"Cuenta {account_number} ahora esta {req.status}"}

    @app.get("/api/accounts/{account_number}/history")
    def get_history(
        account_number: str,
        token: str | None = Query(None),
        authorization: str | None = Header(None),
    ):
        transactions = service.history(access_token(token, authorization), account_number)
        return {
            "transactions": [
                (item.id, item.from_account, item.to_account, float(item.amount), item.type, item.date)
                for item in transactions
            ]
        }

    @app.post("/api/transactions/transfer")
    def transfer_money(
        transfer: TransferRequest,
        authorization: str | None = Header(None),
    ):
        token = transfer.token
        if authorization is not None:
            token = access_token(None, authorization)
        elif not token.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Token faltante o inválido")
        else:
            token = token[7:]
        service.transfer(token, transfer.from_account, transfer.to_account, transfer.amount)
        return {"status": "success", "message": "Transferencia completada"}

    @app.post("/api/transactions/deposit")
    def deposit_money(
        transfer: DepositRequest,
        authorization: str | None = Header(None),
    ):
        token = transfer.token
        if authorization is not None:
            token = access_token(None, authorization)
        if not token or not token.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Token faltante o inválido")
        service.deposit(token[7:], transfer.to_account, transfer.amount)
        return {"message": "Depósito en efectivo registrado"}

    return app
