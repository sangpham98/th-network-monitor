import logging

from app.config import settings


_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(handler)
        return

    for handler in root.handlers:
        handler.setLevel(level)
        if handler.formatter is None:
            handler.setFormatter(logging.Formatter(_FORMAT))
