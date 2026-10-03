"""Bounded bulk commands: dispatch is independent of slow readback verification."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Event

from .commands import MAX_COMMAND_WORKERS, PendingCommand, dispatch_command


def execute_batch(service, targets, action, *, workers=32, cancel=None,
                  experimental_ids=(), on_dispatch=None):
    """Yield (IP, final result); callbacks distinguish accepted from confirmed.

    At most 512 commands are in flight, including waiting readbacks. Duplicate
    IPs are collapsed; each selected device receives at most one write.
    """
    if type(workers) is not int or not 1 <= workers <= MAX_COMMAND_WORKERS:
        raise ValueError('Command workers must be between 1 and 32')
    cancel = cancel if cancel is not None else Event()
    experimental_ids = frozenset(experimental_ids)
    rows = list({row['IP']: dict(row) for row in targets}.values())
    iterator = iter(rows)
    pending = {}
    owned = set()
    exhausted = False
    dispatch_pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='asic-dispatch')
    verify_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix='asic-verify')
    try:
        while pending or not exhausted:
            while not exhausted and len(pending) < 512:
                try:
                    row = next(iterator)
                except StopIteration:
                    exhausted = True
                    break
                future = dispatch_pool.submit(dispatch_command, service, row['IP'], action,
                    device_id=row.get('DeviceId'), cancel=cancel,
                    allow_unverified=row.get('DeviceId') in experimental_ids)
                pending[future] = (row['IP'], 'dispatch')
            if not pending:
                break
            completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                ip, phase = pending.pop(future)
                value = future.result()
                if phase == 'dispatch':
                    if isinstance(value, PendingCommand):
                        owned.add(value)
                        verification = verify_pool.submit(value.complete)
                        pending[verification] = (ip, 'verify')
                    if on_dispatch:
                        # accepted does not mean succeeded; the latter requires readback.
                        on_dispatch(ip, bool(value.accepted) if isinstance(value, PendingCommand)
                                    else value.accepted_by_api)
                if not isinstance(value, PendingCommand):
                    yield ip, value
            owned = {task for task in owned if not task.closed}
    finally:
        # A consumer closing the iterator must not strand a per-device lock or
        # HTTP session, including dispatches finishing after the last yield.
        if pending:
            cancel.set()
        dispatch_pool.shutdown(wait=True)
        for future, (_, phase) in pending.items():
            if phase == 'dispatch' and not future.cancelled() and future.exception() is None:
                value = future.result()
                if isinstance(value, PendingCommand):
                    owned.add(value)
                    verify_pool.submit(value.complete)
        verify_pool.shutdown(wait=True)
        for task in owned:
            task.close()
