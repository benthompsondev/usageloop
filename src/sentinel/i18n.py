"""UI-only translations. Internal state and diagnostic identifiers stay in English.

Language changes take effect on restart. Unknown strings fall back to English.
"""
from __future__ import annotations

import os
from datetime import datetime
from PySide6.QtCore import QLocale, QSettings, QTranslator
from PySide6.QtWidgets import QApplication

from .translations import ZH_CN
from .locales import CATALOGUES, DIALOG_BUTTONS

LANGUAGES = (
    ("system", "System default"), ("en", "English"), ("zh_CN", "简体中文"),
    ("es", "Español"), ("pt_BR", "Português (Brasil)"),
    ("fr", "Français"), ("ja", "日本語"),
)

def language_preference() -> str:
    value = str(QSettings("UsageLoop", "UsageLoop").value("language", "system"))
    return value if value in {key for key, _ in LANGUAGES} else "system"

def save_language(value: str) -> None:
    if value not in {key for key, _ in LANGUAGES}:
        raise ValueError("Unsupported language")
    settings = QSettings("UsageLoop", "UsageLoop")
    previous = settings.value("language", "system")
    settings.setValue("language", value)
    settings.sync()
    if settings.status() != QSettings.Status.NoError:
        settings.setValue("language", previous)
        settings.sync()
        raise OSError("Could not save language preference")

def resolved_language() -> str:
    value = os.environ.get("USAGELOOP_LANGUAGE") or language_preference()
    if value == "system":
        locale = QLocale.system()
        if locale.script() == QLocale.Script.SimplifiedHanScript:
            return "zh_CN"
        value = {
            QLocale.Language.Spanish: "es", QLocale.Language.Portuguese: "pt_BR",
            QLocale.Language.French: "fr", QLocale.Language.Japanese: "ja",
        }.get(locale.language(), "en")
    return value if value == "en" or value in CATALOGUES else "en"

# Stable during a session: switching language cannot change active automation.
_language = "en"


def configure_language() -> None:
    """Initialize once at desktop startup; importing presentation code is inert."""
    global _language
    _language = resolved_language()
    application = QApplication.instance()
    if application is not None:
        old = getattr(application, "_usageloop_translator", None)
        if old is not None:
            application.removeTranslator(old)
        translator = DialogTranslator(application)
        application.installTranslator(translator)
        application._usageloop_translator = translator


class DialogTranslator(QTranslator):
    """Translate Qt's standard dialog buttons without a runtime .qm dependency."""
    def isEmpty(self) -> bool:  # noqa: N802
        return False

    def translate(self, context, sourceText, disambiguation=None, n=-1):
        if _language in DIALOG_BUTTONS and context in {"QPlatformTheme", "QDialogButtonBox", "QMessageBox"}:
            yes, no, cancel, ok, close = DIALOG_BUTTONS[_language]
            def mnemonic(label, key):
                return label + f"(&{key})" if _language in {"zh_CN", "ja"} else "&" + label
            return {"&Yes": mnemonic(yes, "Y"), "Yes": yes, "&No": mnemonic(no, "N"),
                    "No": no, "Cancel": cancel, "&Cancel": mnemonic(cancel, "C"),
                    "OK": ok, "Close": close}.get(sourceText)
        return None

def current_language() -> str:
    return _language


def tr(text: str) -> str:
    if not isinstance(text, str):
        return text
    return CATALOGUES.get(_language, {}).get(text, text)


def ui_locale() -> QLocale:
    """A widget locale only; never change the scheduler's process locale."""
    return QLocale("en_US" if _language == "en" else _language)


def format_datetime(value: datetime, pattern: str) -> str:
    """Use catalogue date patterns; English alone removes AM/PM padding."""
    text = value.strftime(tr(pattern))
    return text if current_language() != "en" else text.lstrip("0").replace(" 0", " ")


