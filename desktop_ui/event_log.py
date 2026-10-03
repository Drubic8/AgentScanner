"""Bounded, plain-text events and explicit export formats, independent of Qt."""
from dataclasses import asdict, dataclass
from datetime import datetime
import csv
import io
import ipaddress
import json
import re

OUTCOMES = ("succeeded", "unconfirmed", "failed", "unsupported", "skipped")
MAX_MESSAGE = 8192


def redact(text):
    text = str(text)
    truncated = len(text) > MAX_MESSAGE
    text = text[:MAX_MESSAGE]
    text = re.sub(r"(?i)(\bBearer\s+)\S+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(\bauthorization[\"']?\s*[:=]\s*)(?:Bearer|Basic)\s+\S+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(\b(?:password|passwd|pwd|token|secret|authorization|api[_-]?key)\b[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)",
                  r"\1[REDACTED]", text)
    text = re.sub(r"(?<![\w+.-])([a-zA-Z][\w+.-]{0,31}://)[^/@\s]+:[^/@\s]+@", r"\1[REDACTED]@", text)
    return text + (" … [truncated]" if truncated else "")


@dataclass(frozen=True)
class LogEvent:
    timestamp: str
    level: str
    ip: str
    action: str
    outcome: str
    message: str

    @classmethod
    def create(cls, message, *, action="", level=None, timestamp=None):
        message = redact(message)
        # Keep old imported-style prefixes as text; the actual event date is captured here.
        match = re.search(r"(?:^|\]\s)(\d{1,3}(?:\.\d{1,3}){3}):\s*(?:\[([a-z_]+)\])?", message)
        ip, outcome = "", ""
        if match:
            try:
                ip = str(ipaddress.ip_address(match[1]))
            except ValueError:
                pass
            outcome = match[2] if match[2] in OUTCOMES else ""
        if level not in ("info", "success", "warning", "error"):
            level = ("success" if outcome == "succeeded" else "error" if outcome == "failed" else
                     "warning" if outcome else "error" if re.search(r"(?i)ошибка|error|failed", message) else "info")
        return cls((timestamp or datetime.now().astimezone()).isoformat(timespec="milliseconds"),
                   level, ip, redact(action), outcome, message)

    def line(self):
        context = " · ".join(part for part in (self.ip, self.action, self.outcome) if part)
        return f"[{self.timestamp}] [{self.level}] {context + ' · ' if context else ''}{self.message}"

    def matches(self, query="", level="", outcome="", action=""):
        haystack = " ".join((self.timestamp, self.ip, self.action, self.outcome, self.level, self.message)).casefold()
        return (all(word in haystack for word in query.casefold().split()) and
                (not level or self.level == level) and
                (not outcome or self.outcome == outcome) and
                (not action or self.action == action))


def export_events(events, kind):
    """Export exactly the supplied scope; CSV cells cannot execute spreadsheet formulas."""
    if kind == "txt":
        return "\n".join(event.line() for event in events) + ("\n" if events else "")
    if kind == "jsonl":
        return "".join(json.dumps(asdict(event), ensure_ascii=False) + "\n" for event in events)
    if kind != "csv":
        raise ValueError("Unsupported event export format")
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    keys = list(LogEvent.__dataclass_fields__)
    writer.writerow(keys)
    for event in events:
        values = asdict(event)
        writer.writerow(["'" + values[key] if values[key].lstrip().startswith(("=", "+", "-", "@"))
                         else values[key] for key in keys])
    return stream.getvalue()
