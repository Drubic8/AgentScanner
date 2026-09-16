"""Compatibility import. Pure parser lives in miner_scanner.parsers.jasminer."""
from ..parsers.jasminer import parse_jasminer

def fetch_jasminer_web(ip):
    from ..runtime import Operation
    from ..transports import Transport
    from ..drivers import query
    with Transport(ip, Operation()) as transport:
        return query(transport, ("jasminer_status", "http_post", "/cgi-bin/minerStatus.cgi", False))
