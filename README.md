# Bancandino Core

API FastAPI con arquitectura hexagonal: `api.py` es el adaptador HTTP, `application.py` contiene los casos de uso, `domain.py` define entidades/puertos y `infrastructure.py` implementa PostgreSQL, JWT y Argon2. El objeto `app` conserva el punto de entrada `main:app`.

## Ejecutar con Docker

En Windows, desde PowerShell, una sola orden crea secretos aleatorios locales si faltan y levanta el stack:

```powershell
.\scripts_utiles\iniciar.ps1
```

La API queda disponible en `http://localhost` detrás de Nginx, enlazado solo a loopback. La contraseña de la cuenta operadora inicial (usuario `cashier` por defecto) se guarda en `secrets/bank_operator_password.txt`; trátala como una credencial y no la copies a registros o tickets. Para cambiar el usuario, define `BANK_OPERATOR_USERNAME` antes de ejecutar el script. Los secretos y el volumen PostgreSQL persisten entre reinicios.

El puerto de PostgreSQL y el puerto interno de la API no se publican en el host. PostgreSQL vive en una red interna; el proceso API usa `bancandino_app`, no el superusuario de inicialización. Nginx y la API se ejecutan con capacidades eliminadas, sin filesystem raíz escribible y con límites de procesos. No se monta el socket Docker.

Para producción, termina TLS en un balanceador/reverse proxy de confianza delante del puerto HTTP del proxy; no expongas tráfico HTTP sin cifrar a redes no confiables. Configura controles de red, copias de seguridad cifradas y rotación de los archivos secretos. El primer arranque genera una base de datos PostgreSQL vacía; no se importan ni reutilizan los datos SQLite legados.

Para detener el stack:

```powershell
docker compose down
```

Los datos PostgreSQL permanecen en el volumen `pgdata`. Para reinicializar el entorno de desarrollo, elimina ese volumen de forma deliberada con `docker compose down --volumes`; esa acción destruye los datos.

## Contratos HTTP

Se mantienen las rutas, métodos, campos de entrada/salida y mensajes de éxito existentes. Login sigue retornando `access_token` y `type: "Bearer"`. Las operaciones sensibles ahora exigen autenticación y autorización:

- `GET /api/clients/me` y `GET /api/clients/profile_backup`: aceptan el token legado `?token=Bearer ...` y también el encabezado `Authorization: Bearer ...`.
- Balance, historial y estado de cuenta aceptan el encabezado Bearer o el parámetro legado `token`; solo el titular o un operador puede consultar la cuenta. Solo un operador puede reactivar una cuenta congelada.
- Transferencias conservan el campo `token` en el JSON. Depósitos conservan la misma ruta/cuerpo, pero requieren token de operador. Los depósitos y transferencias deben ser positivos, finitos y tener como máximo dos decimales.
- El registro requiere contraseña de al menos 12 caracteres. No se crean cuentas ni contraseñas demo.

Los tokens duran una hora. Los usuarios cliente no pueden crear saldo: los depósitos requieren el usuario operador inicial, provisionado con contraseña aleatoria. Nginx limita intentos de login y registro.

El encabezado `Authorization` es preferible a los parámetros de consulta; se conserva el formato legado para compatibilidad. El formato de access log de Nginx registra la ruta sin query string y Uvicorn no genera access logs para no escribir tokens heredados en los logs.

## Pruebas

Instala dependencias de desarrollo y ejecuta:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

Las pruebas inyectan repositorios y servicios falsos para validar lógica y contrato HTTP; incluyen aserciones de los cuerpos exitosos de respuesta heredados (registro, login, perfiles, saldo, historial, estado, transferencia y depósito). Una prueba adicional usa SQLite en memoria para ejercitar transacciones y persistencia del adaptador SQLAlchemy. SQLite solo se usa para pruebas, no en producción.
