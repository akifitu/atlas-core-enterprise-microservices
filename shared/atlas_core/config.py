import os
from datetime import datetime
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")


def env(name: str, default: Optional[T] = None, cast: Callable[[str], T] = str) -> Optional[T]:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value == "":
        return default

    try:
        return cast(raw_value)
    except ValueError as exc:
        raise RuntimeError("Invalid value for environment variable {0}".format(name)) from exc


def service_url(name: str, default_port: int) -> str:
    normalized = name.upper().replace("-", "_")
    return env("{0}_URL".format(normalized), "http://127.0.0.1:{0}".format(default_port))  # type: ignore[arg-type]


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
