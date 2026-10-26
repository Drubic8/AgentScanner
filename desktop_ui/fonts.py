"""Shared desktop typography; sizes are Qt device-independent pixels."""
from PyQt6.QtGui import QFont, QFontDatabase

from .preferences import DEFAULT_FONT_FAMILY


def available_font_family(family):
    """Use an installed family, falling back when settings move to another PC."""
    families = {name.casefold(): name for name in QFontDatabase.families()}
    return (families.get(family.casefold()) or families.get(DEFAULT_FONT_FAMILY.casefold())
            or QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family())


def font_css(family, size):
    # Quote a family as data, including unusual names loaded from settings.
    escaped = family.replace("\\", "\\\\").replace("'", "\\'")
    return f"font-family: '{escaped}'; font-size: {size}px;"


def bold_cell_font(base):
    """Emphasize status without reverting to the application font size."""
    font = QFont(base)
    font.setBold(True)
    return font
