#!/bin/sh
set -eu

fastorder_password="$(cat /run/secrets/fastorder_db_password)"
casdoor_password="$(cat /run/secrets/casdoor_db_password)"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" \
  --set=fastorder_password="$fastorder_password" \
  --set=casdoor_password="$casdoor_password" <<'SQL'
CREATE ROLE fastorder LOGIN PASSWORD :'fastorder_password';
CREATE ROLE casdoor LOGIN PASSWORD :'casdoor_password';
CREATE DATABASE fastorder OWNER fastorder;
CREATE DATABASE casdoor OWNER casdoor;
SQL
