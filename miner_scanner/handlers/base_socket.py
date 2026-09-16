"""Compatibility functions backed by the bounded transport."""
from ..drivers import query
from ..runtime import Operation
from ..transports import Transport

def send_socket_cmd(ip, cmd, raw_mode=False):
    from ..runtime import ScannerError
    try:
        with Transport(ip, Operation()) as transport:
            return transport.cgminer(cmd, raw=raw_mode)
    except (OSError, ScannerError, ValueError):
        return None

def get_socket_data(ip, commands=("version", "stats", "summary", "pools")):
    result = {}
    with Transport(ip, Operation()) as transport:
        for command in commands:
            value = query(transport, (command, "cgminer", command, False))
            if value is not None:
                result[command] = value
    return result
