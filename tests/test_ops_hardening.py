import logging
from pathlib import Path

from app.config import settings
from app.logging_config import configure_logging


def test_configure_logging_is_idempotent(monkeypatch):
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    try:
        root.handlers.clear()
        monkeypatch.setattr(settings, "log_level", "DEBUG")

        configure_logging()
        configure_logging()

        assert len(root.handlers) == 1
        assert root.level == logging.DEBUG
    finally:
        root.handlers.clear()
        root.handlers.extend(original_handlers)


def test_worker_uses_logger_not_prints():
    worker_path = Path("monitor/worker.py")
    content = worker_path.read_text()

    assert "print(" not in content
    assert "logger.info" in content
    assert "logger.warning" in content
    assert "logger.exception" in content


def test_systemd_units_include_hardening_directives():
    for service in [
        Path("systemd/th-network-monitor-web.service"),
        Path("systemd/th-network-monitor-worker.service"),
    ]:
        content = service.read_text()
        assert "User=thnm" in content
        assert "Group=thnm" in content
        assert "WorkingDirectory=/opt/th-network-monitor" in content
        assert "EnvironmentFile=/etc/th-network-monitor/.env" in content
        assert "Environment=THNM_ENV_FILE=/etc/th-network-monitor/.env" in content
        assert "NoNewPrivileges=true" in content
        if service.name == "th-network-monitor-worker.service":
            assert "AmbientCapabilities=CAP_NET_RAW" in content
            assert "CapabilityBoundingSet=CAP_NET_RAW" in content
        assert "PrivateTmp=true" in content
        assert "ProtectSystem=full" in content
        assert "ProtectHome=true" in content
        assert "ReadWritePaths=/var/lib/th-network-monitor /var/log/th-network-monitor" in content
        assert "StandardOutput=journal" in content
        assert "StandardError=journal" in content


def test_logrotate_config_contains_rotation_rules():
    content = Path("systemd/th-network-monitor.logrotate").read_text()

    assert "/var/log/th-network-monitor/*.log" in content
    assert "daily" in content
    assert "rotate 14" in content
    assert "compress" in content
    assert "missingok" in content
    assert "copytruncate" in content
    assert "create 0640 thnm thnm" in content


def test_installer_contains_safety_steps():
    content = Path("scripts/install.sh").read_text()

    assert "set -euo pipefail" in content
    assert "Preserved existing $ENV_FILE" in content
    assert "--filter=':- .gitignore'" in content
    assert "--exclude '.env'" in content
    assert "ADMIN_PASSWORD=$admin_password" in content
    assert "SESSION_SECRET=$session_secret" in content
    assert "require_command ping" in content
    assert "systemctl daemon-reload" in content
    assert "systemctl enable --now" in content
    assert "/usr/local/bin/thnm" in content


def test_thnm_helper_supports_expected_commands():
    content = Path("scripts/thnm").read_text()

    for command in ["status", "start", "stop", "restart", "logs", "web-logs", "worker-logs", "backup", "run-once", "edit-config"]:
        assert f"{command})" in content
    assert "/etc/th-network-monitor/.env" in content
    assert "/opt/th-network-monitor" in content
    assert "PYTHON_CODE" in content
    assert "exec(os.environ" in content


def test_bootstrap_clones_repo_and_runs_installer():
    content = Path("scripts/bootstrap.sh").read_text()

    assert "set -euo pipefail" in content
    assert "Usage: bootstrap.sh <repo-url> [checkout-dir]" in content
    assert "require_command git" in content
    assert "git clone \"$REPO_URL\" \"$CHECKOUT_DIR\"" in content
    assert "git -C \"$CHECKOUT_DIR\" pull --ff-only" in content
    assert "exec \"$CHECKOUT_DIR/scripts/install.sh\"" in content
    assert "https://*|http://*|git@*:*|ssh://*" in content
