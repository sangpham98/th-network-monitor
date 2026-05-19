#!/usr/bin/env bash
set -euo pipefail

APP_NAME="th-network-monitor"
DEFAULT_CHECKOUT_DIR="/tmp/$APP_NAME-bootstrap"
REPO_URL="${1:-}"
CHECKOUT_DIR="${2:-$DEFAULT_CHECKOUT_DIR}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-${3:-}}"

usage() {
    cat <<'USAGE'
Usage: bootstrap.sh <repo-url> [checkout-dir] [admin-password]

Clone or update a TH Network Monitor repository checkout, then run scripts/install.sh.
Set ADMIN_PASSWORD or pass [admin-password] to avoid generating a random admin password.

Examples:
  curl -fsSL https://example.com/bootstrap.sh | sudo ADMIN_PASSWORD='change-me' bash -s -- https://github.com/OWNER/th-network-monitor.git
  sudo scripts/bootstrap.sh https://github.com/OWNER/th-network-monitor.git /tmp/th-network-monitor-bootstrap 'change-me'
USAGE
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "Missing required command: $1" >&2
        exit 1
    }
}

require_root() {
    if [[ $EUID -ne 0 ]]; then
        exec sudo bash "$0" "$@"
    fi
}

validate_repo_url() {
    if [[ -z "$REPO_URL" || "$REPO_URL" == "-h" || "$REPO_URL" == "--help" ]]; then
        usage
        exit 2
    fi

    case "$REPO_URL" in
        https://*|http://*|git@*:*|ssh://*) ;;
        *)
            echo "Unsupported repo URL: $REPO_URL" >&2
            echo "Use https://, http://, ssh://, or git@host:path syntax." >&2
            exit 2
            ;;
    esac
}

checkout_repo() {
    if [[ -d "$CHECKOUT_DIR/.git" ]]; then
        git -C "$CHECKOUT_DIR" fetch --prune
        git -C "$CHECKOUT_DIR" pull --ff-only
    else
        rm -rf "$CHECKOUT_DIR"
        git clone "$REPO_URL" "$CHECKOUT_DIR"
    fi
}

main() {
    require_root "$@"
    validate_repo_url
    require_command git
    require_command sudo

    checkout_repo
    exec "$CHECKOUT_DIR/scripts/install.sh" "$ADMIN_PASSWORD"
}

main "$@"
