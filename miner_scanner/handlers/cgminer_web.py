"""Compatibility facade for generic web discovery."""
def parse_cgminer_web(ip, user=None, pwd=None):
    from ..core import process_ip
    return process_ip(ip)
