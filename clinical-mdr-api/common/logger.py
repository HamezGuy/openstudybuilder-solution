import logging
import logging.config

from fastapi import Request

from common.config import settings
from common.exceptions import MDRApiBaseException
from common.observability_privacy import safe_error


class CustomFormatter(logging.Formatter):
    grey = "\x1b[38;5;240m"
    blue = "\x1b[34m"
    yellow = "\x1b[33m"
    red = "\x1b[31m"
    bold_red = "\x1b[1m\x1b[38;5;196m"
    reset = "\x1b[0m"

    def __init__(self, fmt: str | None = None, colors: bool = True):
        super().__init__()
        if fmt is None:
            fmt = "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s"

        self.fmt = fmt
        self.formats = (
            {
                logging.DEBUG: self.grey + fmt + self.reset,
                logging.INFO: self.blue + fmt + self.reset,
                logging.WARNING: self.yellow + fmt + self.reset,
                logging.ERROR: self.red + fmt + self.reset,
                logging.CRITICAL: self.bold_red + fmt + self.reset,
            }
            if colors
            else {}
        )

    def format(self, record):
        return logging.Formatter(self.formats.get(record.levelno, self.fmt)).format(
            record
        )


LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {"format": "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s"},
        "custom": {
            "()": CustomFormatter,
            "colors": settings.color_logs,
        },
    },
    "filters": {
        "control_plane_privacy": {
            "()": "common.observability_privacy.ObservabilityPrivacyFilter"
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": "DEBUG" if settings.app_debug else "INFO",
            "formatter": "custom",
            "filters": ["control_plane_privacy"],
        },
    },
    "root": {
        "handlers": [
            "console",
        ],
        "level": "DEBUG" if settings.app_debug else "INFO",
    },
    "loggers": {
        "neo4j.notifications": {
            "level": "ERROR",  # silence warning messages from Neo4j db
        },
        "neo4j.io": {
            "level": "INFO",  # decrease messages from Neo4j db even if APP_DEBUG is True
        },
    },
}


def default_logging_config():
    logging.config.dictConfig(LOGGING_CONFIG)


log = logging.getLogger(__name__)


async def log_exception(
    request: Request, exception: MDRApiBaseException | Exception
) -> dict[str, str]:
    """Log one handled failure and RETURN its correlation metadata.

    The return value is what makes the rejection id usable. `safe_error` mints a
    fresh uuid4 on every call, so a caller that wanted the id for the tracing
    span could only get it by calling `safe_error` a second time - which yields a
    DIFFERENT id, and an operator handed two ids for one failure cannot correlate
    the log line with the span. The handlers now reuse this exact dict.
    """

    safe = safe_error(exception)
    status_code = (
        exception.status_code
        if isinstance(exception, MDRApiBaseException)
        else getattr(exception, "status_code", 500)
    )
    log.error(
        "Request failed status=%d type=%s method=%s route=%s errorCode=%s rejectionId=%s",
        status_code,
        exception.__class__.__name__,
        request.method,
        request.url.path,
        safe["errorCode"],
        safe["rejectionId"],
    )
    return safe
