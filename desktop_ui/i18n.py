"""Application text catalog. Device IDs, command IDs and preferences stay language-neutral."""
import json
import re
from string import Formatter
from pathlib import Path

SUPPORTED_LANGUAGES = ("ru", "en")
_language = "ru"
CATALOG = json.loads((Path(__file__).parent / "locales" / "en.json").read_text(encoding="utf-8"))


def set_language(language):
    global _language
    _language = language if language in SUPPORTED_LANGUAGES else "ru"


def get_language():
    return _language


def tr(source, **values):
    text = CATALOG.get(source, source) if _language == "en" else source
    return text.format(**values) if values else text


def tr_error(source):
    """Translate our validation messages, keeping interpolated IPs and user text literal."""
    if _language != "en" or source in CATALOG:
        return tr(source)
    for template, translated in CATALOG.items():
        parts = list(Formatter().parse(template))
        if not any(field for _, field, _, _ in parts) or any(spec or conversion for _, _, spec, conversion in parts):
            continue
        pattern = "".join(re.escape(literal) + (f"(?P<{field}>.*?)" if field else "")
                          for literal, field, _, _ in parts)
        match = re.fullmatch(pattern, source, re.DOTALL)
        if match:
            return translated.format(**match.groupdict())
    return source


def install_qt_translator(app):
    """Translate native Qt buttons and file-dialog controls alongside application text."""
    from PyQt6.QtCore import QTranslator, QLibraryInfo, QLocale
    previous = getattr(app, "_asic_qt_translator", None)
    if previous is not None:
        app.removeTranslator(previous)
    translator = QTranslator(app)
    if _language == "ru":
        directory = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        translator.load("qtbase_ru", directory)
        app.installTranslator(translator)
    app._asic_qt_translator = translator
    QLocale.setDefault(QLocale(_language))
