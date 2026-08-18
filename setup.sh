#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1" >&2
        exit 1
    fi
}

generate_password() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 24
    else
        od -An -N24 -tx1 /dev/urandom | tr -d ' \n'
    fi
}

replace_env_value() {
    key=$1
    value=$2
    temporary_file="${ENV_FILE}.tmp.$$"

    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            "${key}="*) printf '%s=%s\n' "$key" "$value" ;;
            *) printf '%s\n' "$line" ;;
        esac
    done < "$ENV_FILE" > "$temporary_file"

    mv "$temporary_file" "$ENV_FILE"
}

ensure_env_value() {
    key=$1
    value=$2
    if ! grep -q "^${key}=" "$ENV_FILE"; then
        printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    fi
}

require_command docker
require_command uv
require_command id

if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose is required (docker compose)." >&2
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "Docker is installed, but its daemon is not available." >&2
    exit 1
fi

ENV_FILE="$PROJECT_DIR/.env"
umask 077

if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<EOF
RCON_PASSWORD=$(generate_password)
JAVA_OPTS="-Xms1G -Xmx2G"
PUID=$(id -u)
PGID=$(id -g)
EOF
    echo "Created .env with a random RCON password."
else
    if grep -q '^RCON_PASSWORD=replace-' "$ENV_FILE"; then
        replace_env_value RCON_PASSWORD "$(generate_password)"
        echo "Replaced the example RCON password with a random value."
    fi
    ensure_env_value PUID "$(id -u)"
    ensure_env_value PGID "$(id -g)"
    ensure_env_value JAVA_OPTS '"-Xms1G -Xmx2G"'
fi

java_opts=$(grep '^JAVA_OPTS=' "$ENV_FILE" | cut -d= -f2- || true)
case "$java_opts" in
    \"*\"|\'*\') ;;
    "") replace_env_value JAVA_OPTS '"-Xms1G -Xmx2G"' ;;
    *) replace_env_value JAVA_OPTS "\"${java_opts}\"" ;;
esac

chmod 600 "$ENV_FILE"
mkdir -p "$PROJECT_DIR/runtime/logs" "$PROJECT_DIR/scripts"

echo "Installing Python dependencies..."
uv sync

echo "Building and starting the Paper server..."
docker compose up -d --build --wait

echo "Verifying RCON and Skript..."
uv run --env-file "$ENV_FILE" python -c \
    'import asyncio; from skript_mcp.server import RconGateway, Settings; asyncio.run(RconGateway(Settings.from_env()).health_check())'

echo
echo "Skript MCP is ready."
echo "Paper: docker compose logs -f paper"
echo "MCP server: uv run --env-file .env skript-mcp"
