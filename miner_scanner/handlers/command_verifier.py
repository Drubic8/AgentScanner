"""Compatibility verification without alternative field guessing."""
from ..runtime import Operation
from ..transports import Transport

def verify_mode(ip, expected_mode_int, auth_basic=None, auth_digest=None, retries=3, delay=2, *, field=None):
    if field not in ("miner-mode", "bitmain-work-mode"):
        return False
    op = Operation()
    with Transport(ip, op) as transport:
        transport.session.auth = auth_digest or auth_basic
        for _ in range(retries):
            try:
                data = transport.http_json("/cgi-bin/get_miner_conf.cgi") or {}
                if field in data and str(data[field]) == str(expected_mode_int):
                    return True
                op.pause(delay)
            except (OSError, ValueError):
                return False
    return False
