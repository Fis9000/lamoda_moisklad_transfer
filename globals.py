from datetime import datetime
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
from dotenv import load_dotenv
import psycopg2

load_dotenv()

class ResettableFileHandler(logging.FileHandler):
    def __init__(self, filename, max_bytes, encoding="utf-8"):
        self.max_bytes = max_bytes
        super().__init__(
            filename,
            mode="a",
            encoding=encoding
        )

    def emit(self, record):
        try:
            if self.stream is None:
                self.stream = self._open()

            self.stream.flush()

            if os.path.getsize(self.baseFilename) >= self.max_bytes:
                self.stream.close()

                self.stream = open(
                    self.baseFilename,
                    "w",
                    encoding=self.encoding
                )

            super().emit(record)

        except Exception:
            self.handleError(record)

def get_logger(name: str, log_file: str, logs_dir: str = "logs") -> logging.Logger:
    """Логи"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        log_path = Path(logs_dir) / log_file
        log_path.parent.mkdir(parents=True, exist_ok=True)

        handler = ResettableFileHandler(
            log_path,
            max_bytes=1 * 2048 * 2048,
            encoding="utf-8"
        )

        formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s'
        )

        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger

class GlobalConfig:
    host = os.getenv("DB_HOST")
    database = ""
    user = ""
    password = os.getenv("DB_PASSWORD")

    status_string = {"system": "lamoda", "status": "ONLINE"}
