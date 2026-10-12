"""Small local crash reports: stack frames only, never credentials or locals."""
import atexit
from datetime import datetime, timezone
import faulthandler
import os
from pathlib import Path
import sys
import traceback

_stream = None


def enable(directory, version):
    global _stream
    if _stream is not None:
        return Path(_stream.name)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = directory / f"crash-{stamp}-{os.getpid()}.log"
    stream = path.open("x", encoding="utf-8", buffering=1)
    stream.write(f"ASIC Monitor {version}; Python {sys.version.split()[0]}; started UTC {stamp}\n")
    try:
        faulthandler.enable(file=stream, all_threads=True)
    except (OSError, RuntimeError):
        stream.close()
        raise
    _stream = stream  # The descriptor must stay open for native crash handlers.
    previous = sys.excepthook

    def report(kind, value, tb):
        stream.write(f"Unhandled exception: {kind.__name__}\n")
        # Do not persist exception messages: they may contain device responses.
        for frame, line in traceback.walk_tb(tb):
            stream.write(f"  File {frame.f_code.co_filename!r}, line {line}, in {frame.f_code.co_name}\n")
        stream.flush()
        previous(kind, value, tb)

    sys.excepthook = report

    def finish():
        faulthandler.disable()
        stream.write("Normal interpreter shutdown\n")
        stream.close()

    atexit.register(finish)
    # Keep only our own five previous sessions, in addition to this one.
    for old in sorted(directory.glob("crash-*.log"), reverse=True)[6:]:
        try:
            old.unlink()
        except OSError:
            pass
    return path
