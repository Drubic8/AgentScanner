"""Official length-prefixed Whatsminer RPC 3.0 contract (TCP 4433).

Source: docs/whatsminer_official_api.md, https://apidoc.whatsminer.com/.
Read compatibility is separate from per-account write authorization.
"""
import re

from .runtime import AuthenticationError

ACTIONS = {'mining_stop', 'mining_start', 'identify_on', 'identify_off', 'reboot'}


class WhatsminerAccessError(AuthenticationError):
    """Only fixed local messages, never raw API responses or credentials."""


def info_message(response):
    if (not isinstance(response, dict) or type(response.get('code')) is not int
            or response['code'] != 0 or response.get('desc') != 'get.device.info'):
        return None
    msg = response.get('msg')
    if not isinstance(msg, dict):
        return None
    miner, system = msg.get('miner'), msg.get('system')
    if not isinstance(miner, dict) or not isinstance(system, dict):
        return None
    if not isinstance(miner.get('type'), str) or not miner['type'].strip():
        return None
    if type(miner.get('working')) not in (bool, str) or str(miner['working']).lower() not in ('true', 'false'):
        return None
    if not re.fullmatch(r'3\.0\.\d+', str(system.get('api', ''))):
        return None
    return msg


def resolve(response):
    result = {'rules': {}, 'blocked': set(ACTIONS), 'evidence': {},
              'reason': 'Не распознан документированный протокол WhatsMiner RPC 3.0'}
    msg = info_message(response)
    if msg is None:
        return result
    system = msg['system']
    result['evidence'] = {'protocol': 'whatsminer.rpc3.0', 'api': system['api'],
                          'write_switch': str(system.get('apiswitch', ''))}
    if type(system.get('apiswitch')) not in (str, int) or str(system['apiswitch']) != '1':
        result['reason'] = 'WhatsMiner: включите API Write в WhatsminerTool; одного открытого порта недостаточно'
        return result
    if not isinstance(msg.get('salt'), str) or not msg['salt']:
        result['reason'] = 'WhatsMiner не выдал salt для авторизации команд'
        return result
    actions = {'mining_stop', 'mining_start', 'reboot'}
    if system.get('ledstatus') in ('auto', 'manual', 'flash'):
        actions.update({'identify_on', 'identify_off'})
    result['rules'] = dict.fromkeys(actions)
    result['blocked'] -= actions
    result['reason'] = 'Для этой команды WhatsMiner не подтвердил нужный формат API'
    return result
