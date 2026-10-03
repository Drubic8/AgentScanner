"""Jasminer status parsing; units and populated sensor channels come from the API."""
import ipaddress
import re

from ..normalization import explicit_rate, format_rate, number
from ..utils import get_uptime_str


def status_block(value):
    """Firmware revisions return either an object or a list of objects."""
    if isinstance(value, list):
        value = next((item for item in value if isinstance(item, dict)), {})
    return value if isinstance(value, dict) else {}


def sensor_readings(resp):
    boards = status_block(resp.get('boards'))
    count = number(boards.get('fan_num'))
    # Keep reported zero RPM. Do not invent channels missing from the response.
    fans = [rpm for i in range(1, 9)
            if (count is None or i <= count)
            and (rpm := number(boards.get(f'fan{i}'))) is not None]
    temps = []
    items = boards.get('board', [])
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            for key, value in item.items():
                if re.fullmatch(r'asic\d+_temp', key):
                    temp = number(value)
                    if temp is not None:
                        temps.append(temp)
    if not temps:
        summary = status_block(resp.get('summary'))
        temps = [temp for key in ('temp_min', 'temp_max')
                 if (temp := number(summary.get(key))) is not None]
    return fans, sorted(temps)


def parse_jasminer(ip, resp):
    if not isinstance(resp, dict) or not resp:
        return None
    summary = status_block(resp.get('summary'))
    fans, temps = sensor_readings(resp)
    pool_root = resp.get('pools', {})
    pools = pool_root.get('pool', []) if isinstance(pool_root, dict) else pool_root
    pools = [pool for pool in pools if isinstance(pool, dict)] if isinstance(pools, list) else []
    active = next((pool for pool in pools if str(pool.get('status', '')).lower()
                   in ('in use', 'alive')), pools[0] if pools else {})
    pool_url = str(active.get('url') or '')
    pool_url = pool_url.replace('stratum+tcp://', '').replace('stratum+ssl://', '')
    rate, unit = explicit_rate(summary.get('rt'))
    average, average_unit = explicit_rate(summary.get('avg'))
    uptime = number(summary.get('uptime'))
    return {
        'IP': ip, 'Make': 'Jasminer',
        'Model': str(summary.get('miner') or 'Jasminer Unknown'),
        'Uptime': get_uptime_str(uptime) if uptime is not None else '—',
        'Real': format_rate(rate, unit, 'Etchash'),
        'Avg': format_rate(average, average_unit, 'Etchash'),
        'Fan': ' '.join(f'{value:g}' for value in fans) or '—',
        'Temp': ' '.join(f'{value:g}' for value in temps) or '—',
        'Pool': pool_url, 'Worker': str(active.get('user') or ''),
        'SortIP': int(ipaddress.IPv4Address(ip)),
        'Algo': 'Etchash', 'RawHash': rate,
    }
