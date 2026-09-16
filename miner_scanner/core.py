"""Compatibility facade for desktop GUI and external scanner consumers."""
from .runtime import ScanOptions
from .service import default_service


def process_ip(ip, target_makes=None, *, cancel=None, options=None, force_identify=False):
    record = default_service().poll(ip, target_makes, cancel=cancel, options=options, force_identify=force_identify)
    return record.to_legacy() if record is not None else None


def scan_network_range(ip_range_str, target_makes=None, *, cancel=None, options=None, on_result=None, on_progress=None, on_error=None, exclusions=()):
    callback = (lambda record: on_result(record.to_legacy())) if on_result else None
    records = default_service().scan(ip_range_str, target_makes, cancel=cancel, options=options,
                                     on_result=callback, on_progress=on_progress, on_error=on_error, exclusions=exclusions)
    return [record.to_legacy() for record in records]
