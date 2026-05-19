#!/usr/bin/env bash
set -euo pipefail

APP_NAME="th-network-monitor"
APP_DIR="/opt/$APP_NAME"
CONFIG_DIR="/etc/$APP_NAME"
ENV_FILE="$CONFIG_DIR/.env"
DATA_DIR="/var/lib/$APP_NAME"
LOG_DIR="/var/log/$APP_NAME"
SERVICE_USER="thnm"
SERVICE_GROUP="thnm"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_SERVICE="th-network-monitor-web.service"
WORKER_SERVICE="th-network-monitor-worker.service"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-${1:-}}"

require_root() {
    if [[ $EUID -ne 0 ]]; then
        exec sudo bash "$0" "$@"
    fi
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "Missing required command: $1" >&2
        exit 1
    }
}

random_secret() {
    python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
}

ensure_user() {
    if ! getent group "$SERVICE_GROUP" >/dev/null; then
        groupadd --system "$SERVICE_GROUP"
    fi

    if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
        useradd --system --gid "$SERVICE_GROUP" --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
    fi
}

copy_source() {
    install -d -m 0755 "$APP_DIR"
    rsync -a --delete \
        --filter=':- .gitignore' \
        --exclude '.git' \
        --exclude '.claude' \
        --exclude '.env' \
        "$SOURCE_DIR/" "$APP_DIR/"
    chown -R root:root "$APP_DIR"
}

install_python_deps() {
    python3 -m venv "$APP_DIR/.venv"
    "$APP_DIR/.venv/bin/python" -m pip install --upgrade pip
    "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
}

write_env_if_missing() {
    install -d -m 0750 -o root -g "$SERVICE_GROUP" "$CONFIG_DIR"
    if [[ ! -f "$ENV_FILE" ]]; then
        install -m 0640 -o root -g "$SERVICE_GROUP" "$APP_DIR/.env.example" "$ENV_FILE"
        admin_password="${ADMIN_PASSWORD:-$(random_secret)}"
        session_secret="$(random_secret)"
        sed -i \
            -e "s|^DATABASE_URL=.*|DATABASE_URL=sqlite:///$DATA_DIR/network_monitor.db|" \
            -e "s|^DATA_DIR=.*|DATA_DIR=$DATA_DIR|" \
            -e "s|^LOG_DIR=.*|LOG_DIR=$LOG_DIR|" \
            -e "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=$admin_password|" \
            -e "s|^SESSION_SECRET=.*|SESSION_SECRET=$session_secret|" \
            "$ENV_FILE"
        echo "Created $ENV_FILE with generated ADMIN_PASSWORD and SESSION_SECRET. Edit TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID before production alerting."
    else
        echo "Preserved existing $ENV_FILE."
    fi
}

install_runtime_dirs() {
    install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$DATA_DIR" "$DATA_DIR/uploads" "$DATA_DIR/import_previews" "$DATA_DIR/backups"
    install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$LOG_DIR"
}

install_services() {
    install -m 0644 "$APP_DIR/systemd/$WEB_SERVICE" "/etc/systemd/system/$WEB_SERVICE"
    install -m 0644 "$APP_DIR/systemd/$WORKER_SERVICE" "/etc/systemd/system/$WORKER_SERVICE"
    install -m 0644 "$APP_DIR/systemd/th-network-monitor.logrotate" "/etc/logrotate.d/th-network-monitor"
    install -m 0755 "$APP_DIR/scripts/thnm" "/usr/local/bin/thnm"
    systemctl daemon-reload
    systemctl enable --now "$WEB_SERVICE" "$WORKER_SERVICE"
}

main() {
    require_root "$@"
    require_command python3
    require_command rsync
    require_command systemctl
    require_command getent
    require_command useradd
    require_command groupadd
    require_command ping
    require_command gcc

    ensure_user
    install_runtime_dirs
    copy_source
    install_python_deps
    write_env_if_missing
    install_services

    cat <<SUMMARY

TH Network Monitor installed.

App:     $APP_DIR
Config:  $ENV_FILE
Data:    $DATA_DIR
Logs:    $LOG_DIR
URL:     http://localhost:8080

Useful commands:
  thnm status
  thnm logs
  thnm edit-config
  thnm restart

SUMMARY
}

main "$@"
