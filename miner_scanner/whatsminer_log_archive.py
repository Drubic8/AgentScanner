"""Bounded in-memory reading of current WhatsMiner RPC 3 log archives.

Only current miner-state.log/system.log are considered. Configuration files,
historical nested archives and hardware serial-number files are never read.
Nothing is extracted to disk and archive paths never enter the returned text.
"""
import re
import tarfile
import zlib

from .runtime import ProtocolError


MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 512
MAX_TEXT_BYTES = 2 * 1024 * 1024
_CHUNK = 1024 * 1024
_LOG_NAMES = {'miner': 'miner-state.log', 'system': 'system.log'}
_EXTENDED_TYPES = {tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME,
                   tarfile.GNUTYPE_LONGLINK, tarfile.GNUTYPE_SPARSE}
_ANSI = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))')


class MissingLogError(ProtocolError):
    """Valid archive did not contain the requested, supported current log."""


class LogArchiveLimitError(ProtocolError):
    """Compressed/expanded archive or its entry count exceeds a fixed limit."""


def _inflate(archive, operation):
    operation.remaining()
    if not isinstance(archive, bytes) or not archive:
        raise ProtocolError('Invalid WhatsMiner log archive size')
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise LogArchiveLimitError('WhatsMiner log archive exceeds size limit')
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    expanded = bytearray()
    try:
        for offset in range(0, len(archive), 64 * 1024):
            operation.remaining()
            pending = archive[offset:offset + 64 * 1024]
            while pending:
                operation.remaining()
                budget = min(_CHUNK, MAX_EXPANDED_BYTES - len(expanded) + 1)
                chunk = decoder.decompress(pending, budget)
                expanded.extend(chunk)
                if len(expanded) > MAX_EXPANDED_BYTES:
                    raise LogArchiveLimitError('WhatsMiner log archive expands beyond limit')
                pending = decoder.unconsumed_tail
                if decoder.eof:
                    if decoder.unused_data or offset + 64 * 1024 < len(archive):
                        raise ProtocolError('Trailing data in WhatsMiner log archive')
                    break
        operation.remaining()
        # Reaching EOF verifies the gzip CRC and length trailer. Concatenated
        # gzip streams are not part of this reviewed archive format.
        if not decoder.eof or decoder.unused_data:
            raise ProtocolError('Incomplete WhatsMiner log archive')
    except zlib.error:
        raise ProtocolError('Invalid WhatsMiner log archive') from None
    return expanded


def _path(name, *, directory=False):
    if not isinstance(name, str) or len(name) > 256 or '\\' in name or ':' in name:
        raise ProtocolError('Unsafe log archive path')
    if name.startswith('/') or any(ord(char) < 32 for char in name):
        raise ProtocolError('Unsafe log archive path')
    parts = name.split('/')
    if '..' in parts:
        raise ProtocolError('Unsafe log archive path')
    parts = [part for part in parts if part not in ('', '.')]
    if not parts and not directory:
        raise ProtocolError('Empty log archive path')
    return parts


def _log_slice(expanded, kind, operation):
    """Inspect physical TAR headers without processing PAX/long-name bodies.

    TarFile normally processes extended-header bodies before callers inspect a
    member. Reading fixed-size headers avoids that allocation/work here and
    deliberately rejects unsupported archive variants rather than guessing.
    """
    if not expanded or len(expanded) % 512:
        raise ProtocolError('Incomplete TAR log archive')
    cursor, count, selected = 0, 0, None
    while cursor < len(expanded):
        operation.remaining()
        header = bytes(expanded[cursor:cursor + 512])
        if header == b'\0' * 512:
            if cursor + 1024 > len(expanded):
                raise ProtocolError('Incomplete TAR end marker')
            # A second hidden TAR after the end marker is not accepted.
            for offset in range(cursor, len(expanded), _CHUNK):
                operation.remaining()
                if any(expanded[offset:offset + _CHUNK]):
                    raise ProtocolError('Trailing data in TAR log archive')
            return selected
        count += 1
        if count > MAX_MEMBERS:
            raise LogArchiveLimitError('Too many log archive entries')
        try:
            member = tarfile.TarInfo.frombuf(header, encoding='utf-8', errors='strict')
        except (tarfile.HeaderError, UnicodeError, ValueError, OverflowError):
            raise ProtocolError('Invalid TAR log header') from None
        if member.type in _EXTENDED_TYPES or member.sparse is not None:
            raise ProtocolError('Unsupported extended log archive entry')
        parts = _path(member.name, directory=member.isdir())
        start = cursor + 512
        size = member.size
        if type(size) is not int or size < 0:
            raise ProtocolError('Invalid log archive member size')
        if size > MAX_EXPANDED_BYTES:
            raise LogArchiveLimitError('Log archive member exceeds size limit')
        end = start + size
        cursor = start + ((size + 511) // 512) * 512
        if end > len(expanded) or cursor > len(expanded):
            raise ProtocolError('Truncated log archive member')
        # Only regular files at archive root or one '<device>.logs' directory
        # are known. Ignore links/FIFO/devices, configs and nested archives.
        path_matches = (len(parts) == 1 or len(parts) == 2 and parts[0].endswith('.logs'))
        if member.isfile() and path_matches and parts[-1] == _LOG_NAMES[kind]:
            if selected is not None:
                raise ProtocolError('Ambiguous current log in archive')
            selected = (start, end)
    raise ProtocolError('Missing TAR end marker')


def extract_whatsminer_log(archive, kind, *, operation):
    """Return (current log tail, truncated), retaining at most 2 MiB UTF-8.

    A matching empty file returns ('', False). A valid archive without the
    requested current log raises MissingLogError. All network/device/file
    identifiers remain outside error messages and the returned log header.
    """
    operation.remaining()
    if not isinstance(kind, str) or kind not in _LOG_NAMES:
        raise MissingLogError('Unsupported WhatsMiner log kind')
    expanded = _inflate(archive, operation)
    selected = _log_slice(expanded, kind, operation)
    if selected is None:
        raise MissingLogError('Current WhatsMiner log is unavailable')
    start, end = selected
    truncated = end - start > MAX_TEXT_BYTES
    tail_start = max(start, end - MAX_TEXT_BYTES)
    tail = bytes(expanded[tail_start:end])
    operation.remaining()
    if truncated:
        # Prefer complete recent lines; a single oversized line still yields
        # its bounded tail, with the caller visibly marking truncation.
        newline = tail.find(b'\n')
        if 0 <= newline < len(tail) - 1:
            tail = tail[newline + 1:]
    text = tail.decode('utf-8-sig', errors='replace')
    if text.count('\ufffd') > max(8, len(text) // 100):
        raise ProtocolError('Invalid current log encoding')
    text = _ANSI.sub('', text)
    if any(ord(char) < 32 and char not in '\t\n\r' for char in text):
        raise ProtocolError('Binary data in current log')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    encoded = text.encode('utf-8')
    if len(encoded) > MAX_TEXT_BYTES:
        text = encoded[-MAX_TEXT_BYTES:].decode('utf-8', errors='ignore')
        truncated = True
    operation.remaining()
    return text, truncated
