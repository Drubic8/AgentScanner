"""Bounded, previewable support packages. No network or AI calls.

Only allowlisted snapshot fields are exported. Log redaction is deliberately
separate from diagnostics.redact: diagnostic error text must remain readable.
Free-form logs may contain identifiers we cannot recognize; always show the
preview before sharing a package with another person or service.
"""
from datetime import datetime, timezone
import io
import ipaddress
from itertools import islice
import json
import math
from pathlib import Path
import re
from zipfile import ZipFile, ZIP_DEFLATED

from .pools import POOL_FIELDS

MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_PACKAGE_BYTES = 3 * 1024 * 1024
_MASK = "[redacted]"
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u202a-\u202e\u2066-\u2069]")
_URL = re.compile(r"\b(?:[a-z][a-z0-9+.-]{1,24}://|www\.)[^\s<>\"']+", re.I)
_MAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63}\b", re.I)
_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
_IPV6 = re.compile(r"(?<![\w:])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?:%[\w.-]+)?(?![\w:])", re.I)
_MAC = re.compile(r"\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b", re.I)
# Include quoted JSON/Python keys and JSON escaped inside a log message. Mask
# the rest of the line because header values can contain spaces and semicolons.
# Whitespace after ':' also covers pretty-printed JSON with the value on the
# following line; unrelated subsequent diagnostic lines remain intact.
_HEADER = re.compile(
    r"\b(?:authorization|proxy[-_]authorization|cookie|set[-_]cookie)"
    r"(?:\\*[\"'])?\s*[:=]\s*[^\r\n]*", re.I)
_SENSITIVE_VALUE = re.compile(
    r"(?<!\w)((?:\\*[\"'])?(?:pass(?:word|wd)?|pwd|(?:access_|refresh_|api_)?token|"
    r"secret|api[-_]?key|user(?:name)?|worker\d*|wallet|account|pool(?:_url|\d*)?|"
    r"serial(?:_number)?|sn|mac|device[-_]?id)(?:\\*[\"'])?\s*[:=]\s*)"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\r\n]+)", re.I)
_WALLET = re.compile(r"\b(?:0x[0-9a-f]{40}|(?:bc1|tb1)[a-z0-9]{20,90}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b", re.I)
_WINDOWS_PATH = re.compile(r"\b[A-Z]:[\\/][^\r\n\"'<>]*", re.I)
_PERSONAL_PATH = re.compile(r"/(?:home|root|Users)/[^\s\"'<>]+")
_DOMAIN = re.compile(r"(?<![\w.-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:com|net|org|io|ru|club|pool|local|lan)(?::\d{1,5})?(?![\w.-])", re.I)
_ACTIONS = ("led_on", "led_off", "identify_toggle", "reboot", "sleep", "wakeup", "normal", "normal_power", "low", "hem")
_LOG_STATUSES = {"succeeded", "unsupported", "auth_required", "unavailable", "invalid_response", "cancelled", "too_large", "timeout", "error"}


def _text(value, limit=4096):
    return value[:limit] if isinstance(value, str) else ""


def _known_values(row, secrets):
    values = set()
    if isinstance(secrets, str):
        secrets = (secrets,)
    if isinstance(secrets, (list, tuple, set, frozenset)):
        for value in islice(secrets, 128):
            if isinstance(value, str) and value and len(value) <= 4096:
                values.add(value)
    identity = row.get("Identity") if isinstance(row.get("Identity"), dict) else {}
    for container, keys in ((row, ("IP", "MAC", "Mac", "SN", "Serial", "DeviceId", *POOL_FIELDS)),
                            (identity, ("ip", "mac", "serial", "device_id", "fingerprint"))):
        for key in keys:
            value = container.get(key)
            if isinstance(value, str) and value and value not in ("—", "-", "Unknown") and len(value) <= 4096:
                values.add(value)
    return sorted(values, key=len, reverse=True)


def _redactor(values):
    # One pass prevents a short password from matching a replacement marker.
    known = re.compile("|".join(re.escape(value) for value in values)) if values else None

    def clean(value, limit=4096):
        text = _CONTROL.sub("", _ANSI.sub("", _text(value, limit)))
        text = _HEADER.sub("[authentication header redacted]", text)
        text = _URL.sub("[URL redacted]", text)
        text = _MAIL.sub("[email redacted]", text)
        text = _WINDOWS_PATH.sub("[path redacted]", text)
        text = _PERSONAL_PATH.sub("[path redacted]", text)
        text = _SENSITIVE_VALUE.sub(lambda match: match[1] + _MASK, text)
        text = _MAC.sub("[MAC redacted]", text)
        text = _IPV4.sub("[IP redacted]", text)

        def ipv6(match):
            try:
                ipaddress.IPv6Address(match[0].split("%", 1)[0])
            except ValueError:
                return match[0]
            return "[IP redacted]"

        text = _IPV6.sub(ipv6, text)
        text = _WALLET.sub("[wallet redacted]", text)
        text = _DOMAIN.sub("[host redacted]", text)
        return known.sub(_MASK, text) if known else text

    return clean


def _number(value):
    if type(value) in (int, float) and abs(value) < 1e100 and math.isfinite(value):
        return value
    return None


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.isoformat() if stamp.tzinfo is not None else None
    except ValueError:
        return None


def _log_field(log, name, default=None):
    if isinstance(log, dict):
        return log.get(name, default)
    return getattr(log, name, default)


def build_bundle(row, log_result=None, *, app_version="", secrets=()):
    """Return a new sanitized package; never mutate the selected device/log."""
    row = row if isinstance(row, dict) else {}
    identity = row.get("Identity") if isinstance(row.get("Identity"), dict) else {}
    telemetry = row.get("Telemetry") if isinstance(row.get("Telemetry"), dict) else {}
    clean = _redactor(_known_values(row, secrets))
    snapshot = {"device": "device-001", "identity": {}, "telemetry": {}, "display": {}, "pools": {}, "capabilities": {}}
    for display, field in (("Make", "make"), ("Model", "model"), ("Firmware", "firmware"),
                           ("FirmwareVersion", "firmware_version"), ("ApiVersion", "api_version"), ("ProfileId", "profile_id")):
        snapshot["identity"][field] = clean(row.get(display) or identity.get(field), 256) or None
    snapshot["identity"]["identified_at"] = _timestamp(identity.get("identified_at"))
    for field in ("rate", "average_rate", "uptime_seconds"):
        snapshot["telemetry"][field] = _number(telemetry.get(field))
    for field in ("temperatures_c", "fan_rpm"):
        values = telemetry.get(field)
        snapshot["telemetry"][field] = [_number(value) for value in values[:256]] if isinstance(values, (list, tuple)) else []
    for field in ("algorithm", "rate_unit", "mining_state"):
        snapshot["telemetry"][field] = clean(telemetry.get(field), 128) or None
    snapshot["telemetry"]["observed_at"] = _timestamp(telemetry.get("observed_at"))
    snapshot["telemetry"]["stale"] = row.get("Stale") is True or telemetry.get("stale") is True
    led = telemetry.get("identify_enabled", row.get("IdentifyEnabled"))
    snapshot["telemetry"]["identify_enabled"] = led if type(led) is bool else None
    for field in ("Status", "Error", "ErrorDetail", "ErrorDetails", "Algo", "Real", "Avg", "Uptime", "Temp", "Fan"):
        if field in row:
            value = row[field]
            snapshot["display"][field] = clean(value) if isinstance(value, str) else _number(value)
    for field in POOL_FIELDS:
        value = row.get(field)
        snapshot["pools"][field] = _MASK if isinstance(value, str) and value and value != "—" else None
    capabilities = row.get("Capabilities")
    if isinstance(capabilities, dict):
        snapshot["capabilities"] = {action: capabilities[action] for action in _ACTIONS
            if isinstance(capabilities.get(action), str) and capabilities[action] in {"supported", "unsupported", "unverified", "unknown"}}
    log = None
    if log_result is not None:
        raw = _text(_log_field(log_result, "text"), MAX_LOG_BYTES + 1)
        text = clean(raw, MAX_LOG_BYTES + 1)
        encoded = text.encode("utf-8", errors="replace")
        truncated = _log_field(log_result, "truncated") is True or len(encoded) > MAX_LOG_BYTES or len(raw) > MAX_LOG_BYTES
        text = encoded[:MAX_LOG_BYTES].decode("utf-8", errors="ignore")
        kind = _log_field(log_result, "kind")
        status = _log_field(log_result, "status")
        source = _text(_log_field(log_result, "source"), 1024).replace("\\", "/")
        # File metadata must never reveal an absolute local path, query or host.
        source = source.rsplit("/", 1)[-1].split("?", 1)[0].split("#", 1)[0]
        log = {"kind": kind if kind in ("miner", "system", "imported") else "unknown",
               "status": status if isinstance(status, str) and status in _LOG_STATUSES else "error",
               "source": clean(source, 256), "collected_at": _timestamp(_log_field(log_result, "collected_at")),
               "truncated": truncated, "text": text}
    manifest = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                "app_version": clean(app_version, 64), "read_only": True, "device": "device-001",
                "review_required": True,
                "redaction": "Known credentials and device/pool identifiers are masked. Review free-form log text before sharing; automatic redaction cannot identify every personal detail.",
                "log_attached": log is not None, "network_upload_performed": False}
    return {"manifest": manifest, "snapshot": snapshot, "log": log}


def _parts(bundle):
    if not isinstance(bundle, dict) or set(bundle) != {"manifest", "snapshot", "log"}:
        raise ValueError("Invalid diagnostic package")
    if not isinstance(bundle["manifest"], dict) or not isinstance(bundle["snapshot"], dict):
        raise ValueError("Invalid diagnostic package")
    log = bundle["log"]
    if log is not None and (not isinstance(log, dict) or not isinstance(log.get("text"), str)):
        raise ValueError("Invalid diagnostic log")
    metadata = dict(bundle["manifest"])
    if log is not None:
        metadata["log"] = {key: log.get(key) for key in ("kind", "status", "source", "collected_at", "truncated")}
    try:
        parts = {"manifest.json": json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"),
                 "device.json": json.dumps(bundle["snapshot"], ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")}
        if log is not None:
            parts["device.log"] = log["text"].encode("utf-8")
        if len(parts.get("device.log", b"")) > MAX_LOG_BYTES or sum(map(len, parts.values())) > MAX_PACKAGE_BYTES:
            raise ValueError("Diagnostic package is too large")
    except (TypeError, OverflowError, RecursionError, UnicodeError) as exc:
        raise ValueError("Invalid diagnostic package") from exc
    return parts


def bundle_preview(bundle):
    """Show exactly the UTF-8 payloads that write_bundle will put in the ZIP."""
    return "\n\n".join(f"--- {name} ---\n{data.decode('utf-8')}" for name, data in _parts(bundle).items())


def write_bundle(path, bundle):
    """Write a bounded ZIP exclusively; failed writes leave no partial file."""
    parts = _parts(bundle)
    stream = io.BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    # Build in memory before publishing anything, then never overwrite evidence.
    destination = Path(path)
    created = False
    try:
        with destination.open("xb") as output:
            created = True
            output.write(stream.getvalue())
            output.flush()
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise
