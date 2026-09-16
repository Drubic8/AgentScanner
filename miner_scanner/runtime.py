"""Operation-scoped cancellation and a wall-clock network budget."""
from dataclasses import dataclass, field
from threading import Event
import time


class ScannerError(Exception):
    code = "scanner_error"


class Cancelled(ScannerError):
    code = "cancelled"


class DeadlineExceeded(ScannerError):
    code = "deadline"


class ProtocolError(ScannerError):
    code = "protocol_error"


class AuthenticationError(ScannerError):
    code = "auth_required"


@dataclass(frozen=True)
class ScanOptions:
    workers: int = 64
    connect_timeout: float = 1.0
    read_timeout: float = 2.0
    device_timeout: float = 15.0
    profile_ttl: float = 86400.0
    metadata_ttl: float = 300.0
    max_addresses: int = 4096
    max_response_bytes: int = 1_048_576

    def __post_init__(self):
        if not 1 <= self.workers <= 256:
            raise ValueError("workers must be between 1 and 256")
        for key in ("connect_timeout", "read_timeout", "device_timeout", "profile_ttl", "metadata_ttl"):
            if getattr(self, key) <= 0:
                raise ValueError(f"{key} must be positive")
        if self.max_addresses < 1 or self.max_response_bytes < 1:
            raise ValueError("Scan limits must be positive")


@dataclass
class Operation:
    options: ScanOptions = field(default_factory=ScanOptions)
    cancel: Event = field(default_factory=Event)
    started: float = field(default_factory=time.monotonic)
    request_count: int = 0
    errors: list[str] = field(default_factory=list)

    def remaining(self) -> float:
        if self.cancel.is_set():
            raise Cancelled("Operation cancelled")
        left = self.options.device_timeout - (time.monotonic() - self.started)
        if left <= 0:
            raise DeadlineExceeded("Device time budget exhausted")
        return left

    def timeout(self, limit: float | None = None) -> float:
        return max(0.001, min(self.remaining(), limit or self.options.read_timeout))

    def pause(self, seconds: float):
        if self.cancel.wait(min(seconds, self.remaining())):
            raise Cancelled("Operation cancelled")
        self.remaining()
