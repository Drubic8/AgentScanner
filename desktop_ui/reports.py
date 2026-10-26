"""Sorting is applied before export columns are selected."""
import ipaddress
from pathlib import Path
import re
import pandas as pd
from .preferences import COLUMNS
from .spreadsheet_cells import csv_cell


def _number(value):
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(value))
    return float(match.group().replace(",", ".")) if match else float("inf")


def sorted_frame(records, column):
    frame = pd.DataFrame(records)
    if frame.empty:
        return frame
    if column == "IP":
        def key(value):
            try:
                return int(ipaddress.IPv4Address(str(value)))
            except ipaddress.AddressValueError:
                return 2 ** 32
        order = frame.get("IP", pd.Series("", index=frame.index)).map(key)
    elif column == "Uptime":
        def uptime(row):
            seconds = (row.get("Telemetry") or {}).get("uptime_seconds")
            if seconds is not None:
                return seconds
            units = {"d": 86400, "h": 3600, "m": 60, "s": 1}
            parts = re.findall(r"(\d+)\s*([dhms])", str(row.get("Uptime", "")))
            return sum(int(n) * units[u] for n, u in parts) if parts else float("inf")
        order = frame.apply(uptime, axis=1)
    elif column in ("Real HR", "Temp"):
        source = "RawHash" if column == "Real HR" and "RawHash" in frame else "Real" if column == "Real HR" else "Temp"
        order = frame.get(source, pd.Series("", index=frame.index)).map(_number)
    else:
        order = frame.get(column, pd.Series("", index=frame.index)).astype(str).str.casefold()
    return frame.loc[order.sort_values(kind="stable").index]


def export_frame(records, columns, sort_column):
    frame = sorted_frame(records, sort_column).rename(columns={"Real": "Real HR", "Avg": "Avg HR"})
    return frame.reindex(columns=columns, fill_value="").rename(columns=COLUMNS)


def write_spreadsheet(frame, destination):
    """Write external strings literally; preserve numbers and the source frame."""
    if Path(destination).suffix.lower() == '.csv':
        safe = frame.map(csv_cell)
        safe.columns = [csv_cell(column) for column in frame.columns]
        safe.to_csv(destination, index=False, encoding='utf-8-sig')
        return
    # openpyxl otherwise truncates cell text silently before inferring its type.
    values = (value for row in frame.itertuples(index=False, name=None) for value in row)
    if (any(isinstance(value, str) and len(value) > 32767 for value in frame.columns)
            or any(isinstance(value, str) and len(value) > 32767 for value in values)):
        raise ValueError('XLSX: текст ячейки превышает 32767 символов; сохраните отчёт в CSV.')
    with pd.ExcelWriter(destination, engine='openpyxl') as writer:
        frame.to_excel(writer, index=False)
        for sheet in writer.book:
            for row in sheet:
                for cell in row:
                    if isinstance(cell.value, str):
                        cell.data_type = 's'
