#!/bin/sh
set -eu

export APP_DATABASE_PASSWORD="$(cat /run/secrets/db_password)"
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --set=ON_ERROR_STOP=1 <<'SQL'
\getenv app_password APP_DATABASE_PASSWORD
CREATE ROLE bancandino_app LOGIN PASSWORD :'app_password';
GRANT CONNECT ON DATABASE bancandino_prod TO bancandino_app;
GRANT USAGE, CREATE ON SCHEMA public TO bancandino_app;
SQL
unset APP_DATABASE_PASSWORD
