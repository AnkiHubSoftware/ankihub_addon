import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FOLDER = Path(__file__).parent / "logs"
LOG_FOLDER.mkdir(exist_ok=True)

LOG_FILE = LOG_FOLDER / "ankihub.log"


def stdout_handler():
    return logging.StreamHandler(stream=sys.stdout)


def file_handler():
    return RotatingFileHandler(
        LOG_FILE, maxBytes=3000000, backupCount=5, encoding="utf-8"
    )


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(levelname)s %(asctime)s %(module)s "
            "%(process)d %(thread)d %(message)s"
        }
    },
    "handlers": {
        "console": {
            "()": stdout_handler,
            "level": "DEBUG",
            "formatter": "verbose",
        },
        "file": {
            "()": file_handler,
            "level": "DEBUG",
            "formatter": "verbose",
        },
    },
    "root": {
        "level": "DEBUG",
        "handlers": ["console", "file"],
    },
}
