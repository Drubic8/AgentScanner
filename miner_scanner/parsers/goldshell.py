"""Read-only Goldshell MCB telemetry, as used by the device's own dashboard."""
import ipaddress
import re

from ..normalization import format_rate, number
from ..utils import get_uptime_str


def boards(data):
    response = data.get('goldshell_devs')
    rows = response.get('data') if isinstance(response, dict) else None
    # Only accept a complete board array; a partial sum must not look like a
    # measured whole-device rate. An empty response is missing, not zero.
    return rows if isinstance(rows, list) and rows and all(isinstance(b, dict) for b in rows) else []


def algorithm(data):
    config = data.get('goldshell_algo')
    if not isinstance(config, dict):
        return 'Unknown'
    choices = config.get('algos')
    if not isinstance(choices, list) or 'algo_select' not in config:
        return 'Unknown'
    selected = [entry for entry in choices if isinstance(entry, dict)
                and entry.get('id') == config['algo_select']]
    if len(selected) != 1:
        return 'Unknown'
    # The selected ID is local to the firmware: match its name, not ID 0/1.
    return {'blake2b(sc)': 'Blake2B-Sia', 'blake2bsha3(hns)': 'Blake2B+SHA3'}.get(
        str(selected[0].get('name', '')).strip().lower(), 'Unknown')


def sensors(text, unit):
    if not isinstance(text, str) or len(text) > 512:
        return []
    parts = text.split('/')
    result = []
    for part in parts:
        match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*' + re.escape(unit) + r'\s*', part, re.I)
        if not match or (value := number(match[1])) is None:
            return []
        result.append(value)
    return result


def readings(data):
    rows = boards(data)
    def rate(field):
        values = [number(board.get(field)) for board in rows]
        # MCB's dashboard explicitly multiplies these board counters by 1e6.
        return sum(values) * 1e6 if values and all(v is not None for v in values) else None
    elapsed = [value for board in rows if (value := number(board.get('time'))) is not None]
    # MCB repeats the same physical fan tuple on every board. The dashboard
    # uses data[0].fanspeed, not one set of fans for each board.
    fans = sensors(rows[0].get('fanspeed'), 'rpm') if rows else []
    temperatures = [value for board in rows for value in sensors(board.get('temp'), '°C')]
    return rate('hashrate'), rate('av_hashrate'), max(elapsed) * 60 if elapsed else None, fans, temperatures


def parse_goldshell(ip, data):
    info = data.get('goldshell_status') or {}
    current, average, uptime, fans, temperatures = readings(data)
    rows = boards(data)
    response = data.get('goldshell_devs') or {}
    state, error, detail = 'Unknown', '', ''
    if response.get('status') == 2 and rows:
        faults = [(b.get('id'), b.get('minerstatus')) for b in rows if b.get('minerstatus') in (1, 2, 3, 4)]
        if faults:
            error = 'BOARD STATUS'
            detail = '; '.join(f'CPB {board}: {code}' for board, code in faults)
            state = 'Running' if current is not None and current > 0 else 'Error'
        elif all(b.get('minerstatus') == 0 for b in rows) and current is not None and current > 0:
            state = 'Running'
    if state == 'Unknown':
        error = 'STATE UNCONFIRMED'
        detail = 'MCB telemetry does not confirm the mining state; zero hashrate alone is not proof of sleep.'
    pools = data.get('goldshell_pools')
    pools = [p for p in pools if isinstance(p, dict)] if isinstance(pools, list) else []
    pool = next((p for p in pools if p.get('active') is True), pools[0] if pools else {})
    algo = algorithm(data)
    return {
        'IP': ip, 'Make': 'Goldshell', 'Model': str(info.get('model') or 'Goldshell Unknown'),
        'Algo': algo, 'Status': state, 'Error': error, 'ErrorDetails': detail,
        'Real': format_rate(current, 'H/s', algo), 'Avg': format_rate(average, 'H/s', algo),
        'RawHash': current, 'Uptime': get_uptime_str(uptime) if uptime is not None else '—',
        'Fan': ' '.join(f'{v:g}' for v in fans) or '—',
        'Temp': ' '.join(f'{v:g}' for v in temperatures) or '—',
        'Pool': str(pool.get('url') or ''), 'Worker': str(pool.get('user') or ''),
        'SortIP': int(ipaddress.IPv4Address(ip)),
    }
