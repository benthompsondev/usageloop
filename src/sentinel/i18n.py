"""UI-only translations. Internal state and diagnostic identifiers stay in English.

Language changes take effect on restart. Unknown strings fall back to English.
"""
from __future__ import annotations

import os
from datetime import datetime
from PySide6.QtCore import QLocale, QSettings, QTranslator
from PySide6.QtWidgets import QApplication

from .translations import ZH_CN

LANGUAGES = (("system", "System default / 跟随系统"), ("en", "English"), ("zh_CN", "简体中文"))

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
        value = "zh_CN" if QLocale.system().script() == QLocale.Script.SimplifiedHanScript else "en"
    return value if value in {"en", "zh_CN"} else "en"

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
        if _language == "zh_CN" and context in {"QPlatformTheme", "QDialogButtonBox", "QMessageBox"}:
            return {"&Yes": "是(&Y)", "Yes": "是", "&No": "否(&N)",
                    "No": "否", "Cancel": "取消", "&Cancel": "取消(&C)",
                    "OK": "确定", "Close": "关闭"}.get(sourceText)
        return None

def current_language() -> str:
    return _language


def tr(text: str) -> str:
    if _language != "zh_CN" or not isinstance(text, str):
        return text
    return ZH_CN.get(text, text)


def format_datetime(value: datetime, pattern: str) -> str:
    """Keep 24-hour zeroes; English alone removes AM/PM-style padding."""
    text = value.strftime(tr(pattern))
    return text if current_language() == "zh_CN" else text.lstrip("0").replace(" 0", " ")


