"""Keep external text out of spreadsheet formula syntax."""


def csv_cell(value):
    if not isinstance(value, str):
        return value
    # Some spreadsheet importers discard initial whitespace/BOM/control bytes.
    start = 0
    while start < len(value) and (value[start].isspace() or ord(value[start]) < 32 or value[start] == '\ufeff'):
        start += 1
    if value[start:].startswith(('=', '+', '-', '@')) or any(char in '\t\r\n' for char in value[:start]):
        return "'" + value
    return value
