"""Platform independent contracts. No GUI, networking or storage imports."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str = field(repr=False)
    auth: str = "digest"


@dataclass
class DeviceIdentity:
    device_id: str
    ip: str
    make: str
    model: str
    firmware: str
    firmware_version: str | None
    api_version: str | None
    serial: str | None
    mac: str | None
    profile_id: str
    profile_version: int
    fingerprint: str
    identified_at: str = field(default_factory=utc_now)


@dataclass
class TelemetrySnapshot:
    """Numeric values are optional: missing is never converted into zero."""
    observed_at: str = field(default_factory=utc_now)
    algorithm: str = "Unknown"
    rate: float | None = None
    average_rate: float | None = None
    rate_unit: str = "H/s"
    uptime_seconds: float | None = None
    temperatures_c: list[float] = field(default_factory=list)
    fan_rpm: list[float] = field(default_factory=list)
    mining_state: str = "unknown"
    stale: bool = False
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class DeviceRecord:
    identity: DeviceIdentity
    telemetry: TelemetrySnapshot
    # Transitional presentation adapter. Drivers never read these strings back.
    display: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, str] = field(default_factory=dict)

    def to_legacy(self) -> dict:
        result = dict(self.display)
        result.update({
            "IP": self.identity.ip, "Make": self.identity.make,
            "Model": self.identity.model, "DeviceId": self.identity.device_id,
            "ProfileId": self.identity.profile_id,
            "Firmware": self.identity.firmware,
            "FirmwareVersion": self.identity.firmware_version,
            "ApiVersion": self.identity.api_version,
            "Identity": asdict(self.identity), "Telemetry": asdict(self.telemetry),
            "Capabilities": dict(self.capabilities), "Stale": self.telemetry.stale,
        })
        return result


@dataclass(frozen=True)
class CommandResult:
    command_id: str
    status: str
    message: str
    accepted_by_api: bool = False
    device_id: str | None = None
    profile_id: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"

    def to_legacy(self) -> tuple[bool, str]:
        return self.succeeded, f"[{self.status}] {self.message}"
