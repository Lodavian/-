#!/bin/sh
set -eu
umask 077

db_password="$(cat /run/secrets/casdoor_db_password)"
mkdir -p /conf
cat > /conf/app.conf <<EOF
appname = casdoor
httpport = 8000
runmode = prod
SessionOn = true
copyrequestbody = true
showSql = false
driverName = postgres
dataSourceName = user=casdoor password=${db_password} host=postgres port=5432 dbname=casdoor sslmode=disable
dbName = casdoor
origin = ${CASDOOR_ORIGIN}
EOF
chmod 600 /conf/app.conf
exec /server --createDatabase=false
