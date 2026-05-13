"""structlog JSON 로거 설정. import 시 자동 구성."""
import logging
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    """JSON 라인 출력 + ISO 타임스탬프."""
    log_level = getattr(logging, level.upper())
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=log_level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


configure_logging()
