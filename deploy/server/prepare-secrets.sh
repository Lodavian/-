#!/bin/sh
set -eu
umask 077

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo so FastOrder secret files can be owned by container UID 10001." >&2
  exit 1
fi

base_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
secret_dir="$base_dir/secrets"
fastorder_dir="$secret_dir/fastorder"
mkdir -p "$fastorder_dir"
chmod 700 "$secret_dir" "$fastorder_dir"

random_secret() { openssl rand -hex 32; }
postgres_password="$(random_secret)"
fastorder_password="$(random_secret)"
casdoor_password="$(random_secret)"

printf '%s' "$postgres_password" > "$secret_dir/postgres_admin_password"
printf '%s' "$fastorder_password" > "$secret_dir/fastorder_db_password"
printf '%s' "$casdoor_password" > "$secret_dir/casdoor_db_password"
printf 'postgresql://fastorder:%s@postgres:5432/fastorder' "$fastorder_password" > "$fastorder_dir/database_dsn"
openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n' > "$fastorder_dir/account_key"

printf 'Webull App Key: '
IFS= read -r app_key
printf 'Webull App Secret: '
stty -echo
IFS= read -r app_secret
stty echo
printf '\nCasdoor client secret (may be left blank until Casdoor bootstrap): '
stty -echo
IFS= read -r casdoor_client_secret
stty echo
printf '\n'

printf '%s' "$app_key" > "$fastorder_dir/webull_app_key"
printf '%s' "$app_secret" > "$fastorder_dir/webull_app_secret"
printf '%s' "$casdoor_client_secret" > "$fastorder_dir/casdoor_client_secret"

find "$secret_dir" -maxdepth 1 -type f -exec chmod 600 {} \;
find "$fastorder_dir" -maxdepth 1 -type f -exec chmod 600 {} \;
chown -R 10001:10001 "$fastorder_dir"
echo "Secrets prepared. Values were not printed."
