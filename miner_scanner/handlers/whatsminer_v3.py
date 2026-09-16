"""Compatibility facade; protocol and pure parser are separate."""
from ..parsers.whatsminer import parse_whatsminer_data, safe_float
from ..compat import SimpleWhatsminerTCP

def parse_whatsminer_v3(ip, port=4433):
    from ..core import process_ip
    return process_ip(ip, ["MicroBT"])
