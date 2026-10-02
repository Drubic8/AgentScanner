"""Avalon LED protocol and the 1346/MM317 power-control contract.

Source: Canaan-Creative/avalon10-docs, Universal API, section 2.3.
1346 power commands were captured from FMS 3.3.4 (see docs/testing).
Do not extend them to other series based on LED compatibility alone.
"""
import math
import re

import requests

from .runtime import AuthenticationError, DeadlineExceeded, ProtocolError

LED_ACTIONS = {'identify_on', 'identify_off', 'identify_toggle'}
LED_QUERY = 'ascset|0,led,1-255'
POWER_COMMANDS = {
    'mining_stop': 'ascset|0,softoff',
    'mining_start': 'ascset|0,reboot,1',
    'reboot': 'ascset|0,reboot,1',
}


def module_values(stats):
    rows = stats.get('STATS', []) if isinstance(stats, dict) else []
    if not isinstance(rows, list):
        return {}
    modules = [row for row in rows if isinstance(row, dict) and 'MM ID0' in row]
    if len(modules) != 1:
        return {}
    row = modules[0]
    if type(row.get('MM Count')) is not int or row['MM Count'] != 1:
        return {}
    raw = row['MM ID0']
    if not isinstance(raw, str):
        return {}
    pairs = re.findall(r'(\w+)\[([^\]]*)\]', raw)
    if len(dict(pairs)) != len(pairs):
        return {}
    return dict(pairs)


def power_state(stats):
    """Observed board state, not proof of why the boards are idle."""
    values = module_values(stats)
    try:
        elapsed = int(values['Elapsed'])
        rate = float(values['GHSspd'])
        ps = [float(v) for v in values['PS'].split()]
        softoff = int(values['SoftOFF'])
        work = values['SYSTEMSTATU']
    except (KeyError, ValueError, TypeError):
        return None
    if elapsed < 0 or len(ps) != 7 or not all(math.isfinite(v) and v >= 0 for v in [rate, *ps]):
        return None
    off = (softoff > 0 and work.startswith('Work: In Idle,') and rate == 0
           # Powered-off boards reported either 0 or 1 voltage unit on the
           # tested device; require zero current AND zero output power.
           and ps[2] <= 1 and ps[3:5] == [0, 0])
    running = softoff == 0 and work.startswith('Work: In Work,') and rate > 0 and ps[4] > 0
    return {'elapsed': elapsed, 'off': off, 'running': running}


def power_eligible(data):
    if not eligible(data):
        return False
    version = data['version']['VERSION'][0]
    values = module_values(data.get('stats'))
    return (version.get('MODEL') == '1346-110'
            and version.get('PROD') == 'AvalonMiner 1346-110'
            and version.get('HWTYPE') == 'MM4v1_X3'
            and version.get('SWTYPE') == 'MM317'
            and isinstance(version.get('VERSION'), str)
            and bool(version['VERSION'])
            and values.get('Ver') == '1346-110-' + version['VERSION']
            and power_state(data.get('stats')) is not None)


def display_status(data):
    """State precedence for the observed 1346 contract, independent of averages.

    SoftOFF[5] was observed during the requested soft-off transition and sleep.
    SoftOFF[6] also occurred at boot; its cause cannot be inferred from one poll.
    Other series retain their existing detection until their states are known.
    """
    if not power_eligible(data):
        return None
    values = module_values(data['stats'])
    state = power_state(data['stats'])
    if values['SoftOFF'] == '5':
        if state['off']:
            return 'Sleep'
        if values['SYSTEMSTATU'].startswith(('Work: In Idle,', 'Work: In Work,')):
            return 'Stopping'
    elif values['SoftOFF'] == '0':
        if state['running']:
            return 'Running'
        if values['SYSTEMSTATU'].startswith('Work: In Work,'):
            return 'WaitWork'
    return 'Unknown'


def reply(response):
    if not isinstance(response, str):
        return None
    match = re.fullmatch(
        r'STATUS=([A-Z]),When=\d+,Code=(\d+),Msg=([^|]*),Description=[^|]*\|?',
        response.strip().rstrip('\0'),
    )
    if match:
        return match[1], int(match[2]), match[3]
    return None


def led_state(response):
    parsed = reply(response)
    if parsed is None or parsed[:2] != ('I', 118):
        return None
    match = re.fullmatch(r'ASC 0 set info: LED\[([01])\]', parsed[2])
    return int(match[1]) if match else None


def eligible(data):
    versions = data.get('version', {}).get('VERSION', [])
    if not isinstance(versions, list) or len(versions) != 1 or not isinstance(versions[0], dict):
        return False
    version = versions[0]
    stats = data.get('stats', {}).get('STATS', [])
    if not isinstance(stats, list):
        return False
    # One attached miner: no ambiguous multi-module addressing.
    return (str(version.get('PROD', '')).startswith('AvalonMiner ')
            and isinstance(version.get('MODEL'), str)
            and version.get('API') == '3.7'
            and any(isinstance(row, dict) and type(row.get('MM Count')) is int
                    and row['MM Count'] == 1 and 'MM ID0' in row for row in stats))


def resolve(transport, data):
    result = {'rules': {}, 'blocked': set(LED_ACTIONS) | set(POWER_COMMANDS), 'evidence': {}}
    if not eligible(data):
        return result
    try:
        state = led_state(transport.text_command(LED_QUERY))
        if state is not None:
            result['rules'] = dict.fromkeys(LED_ACTIONS)
            result['blocked'].difference_update(LED_ACTIONS)
            result['evidence'] = {'led_api': 'ascset-led-1', 'readback': 'LED[0|1]'}
    except (OSError, requests.RequestException, AuthenticationError, DeadlineExceeded, ProtocolError, ValueError):
        pass
    if power_eligible(data):
        result['rules'].update(dict.fromkeys(POWER_COMMANDS))
        result['blocked'].difference_update(POWER_COMMANDS)
        result['evidence']['power_api'] = '1346-MM317-softoff-reboot-1'
    return result


def power_command(transport, action):
    command = POWER_COMMANDS[action]
    before = power_state(transport.cgminer('stats'))
    if before is None:
        raise ProtocolError('Avalon power state is unavailable')
    accepted = None
    try:
        accepted = reply(transport.text_command(command)) == ('S', 119, 'ASC 0 set OK')
    except (OSError, requests.RequestException):
        pass  # Read back after a lost response; never repeat the write.

    def verify():
        # MM telemetry lags the accepted command (the FMS capture reads it at
        # +10 s). Space the three caller checks within the operation deadline.
        transport.operation.pause(3)
        after = power_state(transport.cgminer('stats'))
        if after is None:
            return False
        if action == 'mining_stop':
            # Idle/error at boot can also report SoftOFF[6]. Require a transition.
            return before['running'] and after['off']
        # Wakeup is a reboot. This verifies restart, not completion of ramp-up.
        return after['elapsed'] < before['elapsed']

    return accepted, verify


def led_command(transport, action):
    if action not in LED_ACTIONS:
        raise ProtocolError('Unknown Avalon LED action')
    current = led_state(transport.text_command(LED_QUERY))
    if current is None:
        raise ProtocolError('Avalon LED state is unavailable')
    target = 1 - current if action == 'identify_toggle' else int(action == 'identify_on')
    accepted = None
    try:
        response = reply(transport.text_command(f'ascset|0,led,1-{target}'))
        accepted = response == ('S', 119, 'ASC 0 set OK')
    except (OSError, requests.RequestException):
        # Never retry a write after a missing response. Read the state instead.
        pass
    return accepted, lambda: led_state(transport.text_command(LED_QUERY)) == target
