# Informe de seguridad y remediación — Bancandino Core

## BAN-SEC-01 — Inyección SQL en autenticación y operaciones financieras

- **Severidad:** Alta
- **Hallazgo:** Los valores del usuario se interpolaban en SQL para login, registro, lectura de cuentas, transferencias, depósitos e historial.
- **Resolución:** Se sustituyó el acceso SQLite por SQLAlchemy ORM, con consultas parametrizadas y transacciones. No se construye SQL a partir de valores de solicitud.
- **Validación:** Pruebas de repositorio y de transferencia con una base de datos aislada en memoria.

## BAN-SEC-02 — Clave JWT comprometida y generador de tokens privilegiados

- **Severidad:** Alta
- **Hallazgo:** La clave de firma estaba codificada en el repositorio y un script generaba tokens de larga duración para un usuario privilegiado.
- **Resolución:** Se eliminó el secreto fijo y el generador de emergencia. La clave JWT se genera aleatoriamente para el despliegue y se monta como secreto Docker; se valida que tenga al menos 32 bytes. Los tokens expiran en una hora.
- **Acción operativa:** Toda clave que se haya usado antes debe considerarse comprometida y no reutilizarse.

## BAN-SEC-03 — Depósitos no autenticados y escalamiento de saldo

- **Severidad:** Alta
- **Hallazgo:** El endpoint de depósito no validaba el token y aceptaba montos negativos, permitiendo acreditar fondos sin autorización.
- **Resolución:** Se exige un JWT con rol `operator`, el operador inicial se provisiona con una contraseña aleatoria secreta y los montos deben ser estrictamente positivos. La actualización de saldo y la transacción contable se confirman atómicamente. Nginx limita login y registro.

## BAN-SEC-04 — Transferencias con monto negativo y carreras de saldo

- **Severidad:** Alta
- **Hallazgo:** Una transferencia negativa invertía el efecto contable; las lecturas y actualizaciones separadas no protegían adecuadamente operaciones concurrentes.
- **Resolución:** Validación estricta de monto/precisión y transacción SQL con bloqueo de filas en orden estable, verificando titularidad, estado y saldo antes de modificar ambas cuentas.

## BAN-SEC-05 — Exposición de cuentas y cambio de estado sin autorización

- **Severidad:** Alta
- **Hallazgo:** Saldo e historial eran públicos; el cambio de estado solo comprobaba un prefijo de texto y no validaba el token.
- **Resolución:** Las lecturas requieren token y se limitan al titular u operador. Los cambios requieren identidad autenticada, se limitan a estados admitidos y solo un operador puede reactivar una cuenta congelada. Se conserva la ruta HTTP y el cuerpo heredado.

## BAN-SEC-06 — Contraseñas reversibles y credenciales demo

- **Severidad:** Alta
- **Hallazgo:** Las contraseñas se almacenaban invertidas y se insertaban usuarios/saldos conocidos al iniciar.
- **Resolución:** Se usa Argon2id mediante `argon2-cffi`; no se crean usuarios de cliente ni cuentas demo. Las contraseñas nuevas exigen 12 caracteres como mínimo y las de operador 16.

## BAN-SEC-07 — PostgreSQL expuesto, secreto fijo y permisos excesivos

- **Severidad:** Alta
- **Hallazgo:** Compose publicaba el puerto de PostgreSQL y usaba una contraseña fija con `postgres`.
- **Resolución:** Se quitó la publicación del puerto; PostgreSQL solo es accesible en una red Docker interna. El secreto de inicialización se genera localmente y el init crea `bancandino_app` con permisos limitados al esquema; la API no utiliza el superusuario.

## BAN-SEC-08 — Socket Docker montado y ejecución de contenedores con privilegios innecesarios

- **Severidad:** Alta
- **Hallazgo:** La API tenía acceso al socket Docker del host y el Dockerfile iniciaba Uvicorn con recarga.
- **Resolución:** Se retiró el socket, el modo reload y los puertos directos de API/DB. La API y Nginx se ejecutan con usuario/capacidades restringidos, filesystem raíz de solo lectura, `no-new-privileges` y límites de procesos.

## BAN-SEC-09 — Backups y utilidades de emergencia peligrosas

- **Severidad:** Media
- **Hallazgo:** El respaldo `main.py.bak` exponía rutas sin autenticación, incluida una ruta de reinicio destructivo; el generador de tokens y los scripts de saldo/monitorización eran incompatibles con el despliegue seguro nuevo.
- **Resolución:** Se retiraron esos archivos de la aplicación distribuida. Se conserva el exportador de rutas, que no accede a datos ni credenciales.

## Decisiones y límites conocidos

- Se seleccionó PostgreSQL 16 para producción, con persistencia en volumen y nueva instalación limpia; no se migra la base SQLite.
- La capa de aplicación depende de un puerto `BankRepository`; los tests inyectan implementaciones falsas para que la lógica se pueda aislar.
- Los logs de acceso omiten el query string y Uvicorn no registra solicitudes para no almacenar tokens enviados por clientes antiguos.
- Nginx escucha HTTP solo en loopback para el despliegue local. En producción es obligatorio terminar TLS en un balanceador/reverse proxy de confianza antes de exponer la API a redes no confiables.
- El script de inicialización PostgreSQL solo se ejecuta con un volumen nuevo. La rotación de contraseñas de roles y la modificación del esquema requieren un procedimiento de migración controlado.
