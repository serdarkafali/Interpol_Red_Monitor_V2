"""Structured logging setup shared by all services."""

import logging
import sys
from typing import Literal

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

LogFormat = Literal["json", "console"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


def _add_service_name(service_name: str) -> Processor:
    """Return a processor that stamps every log record with the service name."""

    def processor(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
        event_dict["service"] = service_name
        return event_dict

    return processor


def configure_logging(
    *,
    service_name: str,
    level: LogLevel = "INFO",
    log_format: LogFormat = "json",
) -> None:
    """Configure structlog and the standard library logging to emit one format.

    Must be called exactly once, at the very start of a service's ``main()``.
    Logs from third-party libraries that use the standard ``logging`` module
    (HTTP clients, Kafka clients, ...) are rendered in the same format.
    """
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_service_name(service_name),
    ]

    renderer: Processor
    if log_format == "json":
        shared_processors.append(structlog.processors.dict_tracebacks)
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structured logger; pass ``__name__`` as the name."""
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger
