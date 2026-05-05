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
        assert "WorkingDirectory=/home/phamsang/Documents/th-network-monitor" in content
        assert "NoNewPrivileges=true" in content
        assert "PrivateTmp=true" in content
        assert "ProtectSystem=full" in content
        assert "ProtectHome=read-only" in content
        assert "ReadWritePaths=/home/phamsang/Documents/th-network-monitor/data /home/phamsang/Documents/th-network-monitor/logs" in content
        assert "StandardOutput=journal" in content
        assert "StandardError=journal" in content


def test_logrotate_config_contains_rotation_rules():
    content = Path("systemd/th-network-monitor.logrotate").read_text()

    assert "/home/phamsang/Documents/th-network-monitor/logs/*.log" in content
    assert "daily" in content
    assert "rotate 14" in content
    assert "compress" in content
    assert "missingok" in content
    assert "copytruncate" in content
