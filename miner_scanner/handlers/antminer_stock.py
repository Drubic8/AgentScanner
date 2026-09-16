"""Compatibility import. Pure parser lives in miner_scanner.parsers.antminer."""
from ..parsers.antminer import parse_antminer_stock

def parse_antminer_web_fallback(ip, user=None, pwd=None):
    from ..core import process_ip
    return process_ip(ip, ["Bitmain"])
