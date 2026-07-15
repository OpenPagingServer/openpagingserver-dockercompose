#!/usr/bin/env python3
import importlib.util
import os
import sys
from pathlib import Path

OPS_DIR = Path("/opt/OpenPagingServer")
SCRIPT_PATH = OPS_DIR / "scripts" / "database-initialization.py"
OUTPUT_DIR = Path("/opt/ops_env")
ENV_FILE = OUTPUT_DIR / ".env"

spec = importlib.util.spec_from_file_location("dbinit", SCRIPT_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _parse_env_file(path):
    """Read a .env file and return a dict of key=value pairs."""
    values = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip("'\"")
        values[key.strip()] = val
    return values


def _existing_db_creds():
    """Return (host, port, user, password, db_name) from env vars or volume .env, or None."""
    # Check environment variables first (passed from host .env via docker-compose)
    env_user = os.environ.get("APP_DB_USER", "").strip()
    env_pass = os.environ.get("APP_DB_PASS", "").strip()
    env_name = os.environ.get("APP_DB_NAME", "").strip()
    if env_user and env_pass and env_name:
        host = os.environ.get("APP_DB_HOST", "127.0.0.1").strip()
        port = int(os.environ.get("APP_DB_PORT", "3306"))
        return host, port, env_user, env_pass, env_name

    # Fall back to volume .env file
    env = _parse_env_file(ENV_FILE)
    user = env.get("DB_USER", "").strip()
    password = env.get("DB_PASS", "").strip()
    db_name = env.get("DB_NAME", "").strip()
    if not (user and password and db_name):
        return None
    host = env.get("DB_HOST", os.environ.get("APP_DB_HOST", "127.0.0.1")).strip()
    port = int(env.get("DB_PORT", os.environ.get("APP_DB_PORT", "3306")))
    return host, port, user, password, db_name


def patched_connect_as_admin():
    import mysql.connector
    creds = _existing_db_creds()
    host = os.environ.get("APP_DB_HOST", "127.0.0.1")
    port = int(os.environ.get("APP_DB_PORT", "3306"))

    if creds:
        db_host, db_port, db_user, db_pass, _ = creds
        print(f"Found existing database credentials in {ENV_FILE}, connecting as '{db_user}'")
        try:
            return mysql.connector.connect(
                user=db_user, password=db_pass,
                host=db_host, port=db_port,
            )
        except mysql.connector.Error:
            print("Existing credentials failed, falling back to root")

    print("No existing credentials found, connecting as root (no password)")
    return mysql.connector.connect(user="root", host=host, port=port)

mod.connect_as_admin = patched_connect_as_admin


def patched_recreate_database_user(cursor, db_password):
    hosts = ["localhost", "127.0.0.1", "%"]
    for host in hosts:
        cursor.execute(f"DROP USER IF EXISTS '{mod.DATABASE_USER}'@'{host}'")
        cursor.execute(f"CREATE USER '{mod.DATABASE_USER}'@'{host}' IDENTIFIED BY {mod.sql_string(db_password)}")
        cursor.execute(f"GRANT ALL PRIVILEGES ON `{mod.DATABASE_NAME}`.* TO '{mod.DATABASE_USER}'@'{host}'")
    cursor.execute("FLUSH PRIVILEGES")

mod.recreate_database_user = patched_recreate_database_user


def patched_write_config(db_password):
    db_host = os.environ.get("APP_DB_HOST", "127.0.0.1")
    env_content = f"""DB_HOST='{db_host}'
DB_USER='{mod.DATABASE_USER}'
DB_PASS={mod.sql_string(db_password)}
DB_NAME='{mod.DATABASE_NAME}'
DEBUG=false
WEB_REVERSE_PROXY_ALLOWED=127.0.0.1
API_REVERSE_PROXY_ALLOWED=127.0.0.1
DEMO_MODE=false

"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text(env_content, encoding="utf-8")
    (OUTPUT_DIR / ".oobe").write_text("", encoding="utf-8")
    os.makedirs("/var/lib/openpagingserver/assets", exist_ok=True)
    (OPS_DIR / ".env").write_text(env_content, encoding="utf-8")
    (OPS_DIR / ".oobe").write_text("", encoding="utf-8")

mod.write_config = patched_write_config


def patched_select_port(default_port, fallback_ports, port_available, protocols, label):
    """In Docker mode ports are mapped externally via .env — always use defaults."""
    return default_port

# Docker handles port mapping; skip port-availability scanning
mod.select_port = patched_select_port


def patched_main():
    """
    If existing DB creds are present, reconnect with them and only update the
    schema (idempotent). Otherwise run full initialization with a new password.
    """
    import mysql.connector
    creds = _existing_db_creds()
    conn = None
    cursor = None

    try:
        conn = mod.connect_as_admin()
        cursor = conn.cursor()

        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{mod.DATABASE_NAME}` "
            f"DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
        )
        cursor.execute(f"USE `{mod.DATABASE_NAME}`")

        if creds:
            # Existing creds — reuse the password, just update schema & defaults
            _, _, _, db_password, _ = creds
            print("Updating schema with existing credentials")
        else:
            # Fresh install — create user with new password
            db_password = mod.random_password()
            mod.recreate_database_user(cursor, db_password)

        mod.execute_schema(cursor)
        webserver_http_port = mod.select_port(
            mod.DEFAULT_WEB_PORT, mod.WEB_PORT_FALLBACKS,
            mod.web_port_available, ["tcp"], "Web",
        )
        insecure_sip_port = mod.select_port(
            mod.DEFAULT_SIP_PORT, mod.SIP_PORT_FALLBACKS,
            mod.sip_port_available, ["tcp", "udp"], "SIP",
        )
        mod.seed_defaults(cursor, webserver_http_port, insecure_sip_port)

        conn.commit()
        mod.write_config(db_password)
        mod.install_project_root_ca()
        print("Database initialized successfully")
    except mysql.connector.Error as exc:
        if conn:
            conn.rollback()
        print("Database setup failed:", exc)
        sys.exit(1)
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


patched_main()
