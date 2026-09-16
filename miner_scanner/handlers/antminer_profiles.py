"""Compatibility payload helpers. Unknown signatures are rejected."""
from ..commands import mode_payload

def detect_antminer_profile(config):
    if not isinstance(config, dict):
        return "Unknown"
    keys = {"bitmain-work-mode", "miner-mode"} & config.keys()
    if len(keys) != 1:
        return "Unknown"
    return "Modern" if "bitmain-work-mode" in keys else "Legacy"

def normalize_mode_payload(config, target_mode_int, model=""):
    return mode_payload(config, target_mode_int, model)[0]
