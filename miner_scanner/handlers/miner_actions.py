"""Legacy tuple-returning command facade. Strings never select a firmware."""
from ..commands import execute_command
from ..service import default_service

def send_command(ip, make, action, model="", *, device_id=None, allow_unverified=False):
    return execute_command(default_service(), ip, action, device_id=device_id,
                           allow_unverified=allow_unverified).to_legacy()
