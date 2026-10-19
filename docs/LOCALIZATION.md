# Localization

The desktop supports `en`, `zh_CN`, and `system` preferences. The preference is stored using `QSettings("UsageLoop", "UsageLoop")`, separately from automation state. The desktop entry point calls `configure_language()` once at startup. Imports and CLI use remain English and do not read or write language settings.

Language changes require a full exit and restart, including exiting the tray app. `system` selects Simplified Chinese for a Simplified Han system locale and otherwise falls back to English. `USAGELOOP_LANGUAGE=en|zh_CN|system` overrides the saved preference for previews and development without saving it.

## Changing copy

- Keep English source strings in `tr("English source text")`. Add their Simplified Chinese equivalents to `src/sentinel/translations.py`.
- For interpolated text, translate the complete template before formatting: `tr("Version {0} is available.").format(version)`. Preserve fields, conversions, and format specifications.
- Translate at presentation boundaries. Never translate protocol identifiers, persistence keys, status values written to provider state, scheduler decisions, model IDs, command lines, URLs, or user-provided release notes.
- Technical diagnostics stay English for maintainers. Unknown strings fall back to English.
- The catalogue is a Python module, so normal source and PyInstaller dependency discovery include it without extra data-file packaging or a translation compiler.

The small `DialogTranslator` handles Qt standard confirmation buttons. Chinese time editors use a Chinese locale and a 24-hour display; English editors use an English locale and AM/PM, independent of the host's locale. Chinese styling prefers Microsoft YaHei UI or Noto CJK fonts when available; the application does not download fonts.

## Validation

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

`test_i18n.py` checks fallback, saved preference validation, system-script selection, placeholders, Chinese time input, confirmation button text, presentation decision fields, and the separation between Chinese display text and internal provider states. UI tests use fake providers and isolated settings; they do not send Codex requests.

For visual review on Windows, use the native Qt platform so installed fonts are rendered correctly:

```powershell
$env:USAGELOOP_LANGUAGE = 'zh_CN'
$env:QT_QPA_PLATFORM = 'windows'
python scripts/capture-ui-screenshots.py preview-zh-CN
```

The screenshot script uses synthetic usage data and never contacts Codex or GitHub. Review Dashboard, Settings (including daily and expanded weekly routines), About, updates, history, and the narrow-window captures. Existing English screenshots should not be overwritten by translated previews.
