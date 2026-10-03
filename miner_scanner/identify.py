"""Read-only locate-indicator telemetry. Missing state stays unknown."""
import requests

from .runtime import AuthenticationError, DeadlineExceeded, ProtocolError


def boolean(value):
    return value if type(value) is bool else None


def read_state(control, transport, data=None):
    data = data or {}
    try:
        if control in ('antminer', 'pitbit'):
            response = transport.http_json('/cgi-bin/get_blink_status.cgi')
            return boolean(response.get('blink')) if isinstance(response, dict) else None
        if control == 'elphapex':
            response = transport.http_json('/cgi-bin/luci/get_blink_status.cgi')
            return boolean(response.get('blink')) if isinstance(response, dict) else None
        if control == 'vnish':
            response = transport.http_json('/api/v1/status')
            return boolean(response.get('find_miner')) if isinstance(response, dict) else None
        if control == 'whatsminer':
            from .whatsminer_compatibility import info_message
            info = info_message(data.get('rpc_info'))
            status = info.get('system', {}).get('ledstatus') if info else None
            return True if status in ('manual', 'flash') else False if status == 'auto' else None
        if control == 'avalon':
            from .avalon_compatibility import LED_QUERY, led_state
            state = led_state(transport.text_command(LED_QUERY))
            return bool(state) if state is not None else None
    except (OSError, requests.RequestException, AuthenticationError, DeadlineExceeded,
            ProtocolError, ValueError):
        transport.operation.errors.append('identify:unavailable')
    return None
