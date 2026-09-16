"""Compatibility manufacturer lookup. Positive identification lives in profiles."""
def get_miner_make(ip, port=4028):
    from .core import process_ip
    result = process_ip(ip)
    return result["Make"] if result else "Unknown"
