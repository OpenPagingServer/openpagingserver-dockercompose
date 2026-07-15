#!/bin/sh
set -e

if [ -f /opt/ops_env/.env ]; then
    cp /opt/ops_env/.env /opt/OpenPagingServer/.env
fi
if [ -f /opt/ops_env/.oobe ]; then
    cp /opt/ops_env/.oobe /opt/OpenPagingServer/.oobe
fi

if [ ! -d /var/lib/openpagingserver/assets/.git ]; then
    git clone --depth 1 https://github.com/OpenPagingServer/assets.git /var/lib/openpagingserver/assets 2>/dev/null || true
fi

DB_HOST="${APP_DB_HOST:-127.0.0.1}"
if [ -f /opt/OpenPagingServer/.env ]; then
    DB_HOST=$(grep -oP "^DB_HOST='?\K[^']*" /opt/OpenPagingServer/.env 2>/dev/null || echo "$DB_HOST")
fi

TRIES=0
MAX_TRIES=60
while ! python -c "import socket; s=socket.create_connection(('${DB_HOST}', 3306), 1); s.close()" 2>/dev/null; do
    TRIES=$((TRIES+1))
    if [ "$TRIES" -ge "$MAX_TRIES" ]; then
        echo "ERROR: Database not reachable at ${DB_HOST}:3306 after ${MAX_TRIES}s"
        exit 1
    fi
    sleep 1
done

# Auto-initialize database if no credentials exist yet
if [ ! -f /opt/ops_env/.env ]; then
    echo "No database credentials found — running automatic database initialization..."
    python /opt/docker-init-db.py
    # Copy the freshly generated .env into the app directory
    if [ -f /opt/ops_env/.env ]; then
        cp /opt/ops_env/.env /opt/OpenPagingServer/.env
    fi
    if [ -f /opt/ops_env/.oobe ]; then
        cp /opt/ops_env/.oobe /opt/OpenPagingServer/.oobe
    fi
fi

cd /opt/OpenPagingServer
exec "$@"
