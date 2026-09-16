"""Declarative profile registry and positive, structured identification."""
from dataclasses import dataclass
from importlib.resources import files
import json
import re


IDENTITY_KEYS = {"type", "model", "minertype", "miner", "product_type", "prod", "g-model", "ver", "fw_name", "fw_version", "firmware", "firmware_version", "miner_version", "compiletime", "system_filesystem_version"}


def identity_values(data):
    """Exclude arbitrary pool URLs, worker names and diagnostics from fingerprints."""
    result = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key.lower() in IDENTITY_KEYS and isinstance(value, (str, int, float)):
                result.append(str(value))
            elif isinstance(value, (dict, list)) and key.lower() not in {"pools", "rpc_pools", "config", "elphapex_config"}:
                result.extend(identity_values(value))
    elif isinstance(data, list):
        for item in data:
            result.extend(identity_values(item))
    return result


def first_field(data, names):
    if isinstance(data, dict):
        for key, value in data.items():
            if key.lower() in names and isinstance(value, (str, int, float)) and str(value).strip():
                return str(value)
        for key, value in data.items():
            if key.lower() not in {"pools", "config", "rpc_pools", "elphapex_config"}:
                found = first_field(value, names)
                if found is not None:
                    return found
    elif isinstance(data, list):
        for item in data:
            found = first_field(item, names)
            if found is not None:
                return found
    return None


def signatures(data):
    text = " ".join(identity_values(data)).lower()
    found = set()
    stats = data.get("stats", {}).get("STATS", [])
    summary = data.get("summary", {}).get("SUMMARY", [])
    if isinstance(stats, list) and stats or isinstance(summary, list) and summary:
        found.add("cgminer")
    if "vnish" in text:
        found.add("vnish")
    if "pitbit" in text or "incm" in text:
        found.add("pitbit")
    if "antminer" in text or "bitmain" in text:
        found.add("antminer")
    system = data.get("system", {})
    if isinstance(system, dict) and system.get("minertype") and re.match(r"^(S19|S21|T19|T21|L7|L9|D7|D9|Z11|Z15)\b", str(system["minertype"]), re.I):
        found.add("antminer")
    if "avalon" in text or "ava100" in text:
        found.add("avalon")
    if "ipollo" in text or any(isinstance(x, dict) and ("G-Model" in x or x.get("ID") in {"G220", "G1", "V1", "X1", "B1"}) for x in stats):
        found.add("ipollo")
    rpc = data.get("rpc_info", {})
    if rpc.get("code") == 0 and isinstance(rpc.get("msg"), dict):
        msg = rpc["msg"]
        if isinstance(msg.get("miner"), dict) and (msg["miner"].get("type") or "working" in msg["miner"]):
            found.add("whatsminer")
        elif "whatsminer" in text or "microbt" in text:
            found.add("whatsminer")
    if "elphapex" in text or re.search(r"\bdg\d", text):
        found.add("elphapex")
    jas = data.get("jasminer_status", {})
    if "jasminer" in " ".join(identity_values(jas)).lower() and isinstance(jas.get("summary"), (dict, list)):
        found.add("jasminer")
    if 'id="bb_elapsed"' in data.get("web_status", ""):
        found.add("cgminer_web")
    return found


@dataclass(frozen=True)
class Profile:
    id: str
    version: int
    make: str
    firmware: str
    signature: str
    priority: int
    parser: str
    control: str | None
    queries: tuple
    verified_commands: tuple = ()
    match: dict | None = None
    command_rules: dict | None = None
    metrics: dict | None = None

    def matches(self, data):
        if self.signature not in signatures(data):
            return False
        match = self.match or {}
        for key, fields in (("firmware_versions", {"fw_version", "firmware_version", "system_filesystem_version"}), ("api_versions", {"api", "api_ver", "api_version"})):
            if key in match and first_field(data, fields) not in match[key]:
                return False
        if "models" in match and first_field(data, {"model", "minertype", "type", "product_type"}) not in match["models"]:
            return False
        return True


class ProfileRegistry:
    def __init__(self, profiles=None):
        if profiles is None:
            catalog = json.loads(files(__package__).joinpath("catalog.json").read_text(encoding="utf-8"))
            if catalog.get("schema_version") != 1:
                raise ValueError("Unsupported profile schema")
            profiles = [Profile(**{**p, "queries": tuple(tuple(q) for q in p["queries"]), "verified_commands": tuple(p.get("verified_commands", []))}) for p in catalog["profiles"]]
        self.profiles = tuple(profiles)
        self.by_id = {p.id: p for p in self.profiles}
        if len(self.by_id) != len(self.profiles):
            raise ValueError("Duplicate profile ID")
        for profile in self.profiles:
            if profile.version < 1 or not profile.id:
                raise ValueError("Invalid profile identity")
            for query in profile.queries:
                if len(query) not in (4, 5) or query[1] not in {"cgminer", "http", "http_post", "rpc", "html", "text"}:
                    raise ValueError(f"Invalid query in {profile.id}")
                if len(query) == 5 and (set(query[4]) - {"port"} or not 1 <= query[4].get("port", 80) <= 65535):
                    raise ValueError("Invalid query transport options")
            if profile.verified_commands and not (profile.match and profile.match.get("firmware_versions") and profile.match.get("models")):
                raise ValueError("Verified control requires explicit models and firmware versions")
            for action, rule in (profile.command_rules or {}).items():
                if rule.get("method", "POST") not in {"GET", "POST"}:
                    raise ValueError("Unsupported command method")
                for path in (rule.get("path", ""), rule.get("verify", {}).get("path", "")):
                    if not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path:
                        raise ValueError("A command rule requires local write and verification paths")
                if not rule.get("verify", {}).get("field") or "equals" not in rule["verify"]:
                    raise ValueError("Command verification requires a field and expected value")
                if action not in {"identify_on", "identify_off", "reboot", "mining_stop", "mining_start", "low", "normal_power", "hem"}:
                    raise ValueError("Unknown action")
            for metric, mapping in (profile.metrics or {}).items():
                if metric not in {"rate", "average_rate"} or mapping.get("unit") not in {"H/s", "MH/s", "GH/s", "TH/s", "Sol/s", "kSol/s"} or not mapping.get("path"):
                    raise ValueError("Invalid explicit metric mapping")

    def resolve(self, data):
        matches = sorted((p for p in self.profiles if p.matches(data)), key=lambda p: p.priority, reverse=True)
        if not matches:
            return None
        makes = {p.make for p in matches if p.make != "Unknown"}
        if len(makes) > 1:
            return None
        if {p.signature for p in matches} >= {"pitbit", "vnish"}:
            return None
        if len(matches) > 1 and matches[0].priority == matches[1].priority:
            return None
        return matches[0]
