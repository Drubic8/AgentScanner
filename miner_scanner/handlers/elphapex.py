"""Compatibility facade for Elphapex discovery."""
from ..parsers.elphapex import parse_elphapex

def scan_elphapex(ip, user=None, pwd=None, port_9588_open=False):
    from ..core import process_ip
    return process_ip(ip, ["Elphapex"])
