"""Credential selection rules, independent of UI and platform secret storage."""
from dataclasses import dataclass, field
from ipaddress import ip_address, ip_network
from .models import Credentials

FAMILIES = ('antminer', 'vnish', 'whatsminer', 'elphapex', 'other')


@dataclass(frozen=True)
class AccessProfile:
    name: str
    family: str
    username: str
    password: str = field(repr=False)
    auth: str = 'digest'
    targets: str = '*'
    enabled: bool = True

    def __post_init__(self):
        if not self.name.strip() or self.family not in FAMILIES:
            raise ValueError('Укажите название и тип прошивки профиля.')
        if self.auth not in ('digest', 'basic'):
            raise ValueError('Неизвестный тип HTTP-авторизации.')
        if self.family != 'vnish' and not self.username.strip():
            raise ValueError('Укажите логин.')
        if self.targets.strip() != '*':
            parts = self.targets.replace(';', ',').replace('\n', ',').split(',')
            if not parts or any(not p.strip() for p in parts):
                raise ValueError('Укажите IP/подсети через запятую или * для всех адресов.')
            try:
                if any(ip_network(p.strip(), strict=False).version != 4 for p in parts):
                    raise ValueError()
            except ValueError:
                raise ValueError('Ожидаются IPv4-адреса или подсети, например 10.33.6.0/24.') from None

    def matches(self, ip):
        return self.enabled and self.applies_to(ip)

    def applies_to(self, ip):
        """Address scope independently of enablement, including explicit opt-outs."""
        if self.targets.strip() == '*':
            return True
        parts = self.targets.replace(';', ',').replace('\n', ',').split(',')
        return any(ip_address(ip) in ip_network(p.strip(), strict=False) for p in parts)

    @property
    def credentials(self):
        return Credentials(self.username, self.password, self.auth)


def standard_profiles():
    return (
        AccessProfile('Antminer — стандартный', 'antminer', 'root', 'root'),
        AccessProfile('VNish — admin', 'vnish', '', 'admin'),
        AccessProfile('VNish — root', 'vnish', '', 'root'),
        # Official docs/Whatsminer demo/whatsminer.py uses this API default.
        AccessProfile('WhatsMiner — все устройства', 'whatsminer', 'super', 'super'),
    )


def access_family(profile_id):
    if profile_id == 'bitmain.vnish':
        return 'vnish'
    if profile_id and profile_id.startswith('bitmain.'):
        return 'antminer'
    return {'microbt.rpc3': 'whatsminer', 'elphapex.luci': 'elphapex'}.get(profile_id, 'other')


class AccessProfiles:
    def __init__(self, profiles):
        self.profiles = tuple(profiles)
        if len(self.profiles) > 100:
            raise ValueError('Допускается не более 100 профилей доступа.')

    def candidates(self, ip, family=None):
        # Unknown devices use only Antminer discovery credentials. VNish passwords
        # are used only after its REST API has positively identified the firmware.
        family = family or 'antminer'
        # Jasminer and other CGI devices can be discovered with the HTTP
        # discovery profile. Retain that fallback after identification/restart,
        # unless the user configured an Other profile for this address. A
        # disabled Other profile is an explicit opt-out, not a fallback trigger.
        if family == 'other' and not any(
                p.family == 'other' and p.applies_to(ip) for p in self.profiles):
            family = 'antminer'
        matches = [p for p in self.profiles if p.family == family and p.matches(ip)]
        matches.sort(key=lambda p: p.targets.strip() == '*')
        result = []
        for profile in matches:
            if profile.credentials not in result:
                result.append(profile.credentials)
        return result[:3]
