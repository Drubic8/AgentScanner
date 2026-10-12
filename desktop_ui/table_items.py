"""Deterministic ordering for numeric cells, including missing/malformed values."""
from math import isfinite

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTableWidgetItem


def sort_key(item):
    value = item.data(Qt.ItemDataRole.UserRole)
    if value is None:
        parts = item.text().split()
        value = parts[0] if parts else None
    try:
        number = float(value)
        if isfinite(number):
            return (0, number, "")
    except (TypeError, ValueError, OverflowError):
        pass
    return (1, 0, item.text())


class SmartSortItem(QTableWidgetItem):
    def __lt__(self, other):
        # A key per item is essential: choosing numeric/text order per pair
        # creates cycles (2 < 10 < "1a" < 2) and violates Qt's sort contract.
        return sort_key(self) < sort_key(other)
