# Localization

The 1.4.0 desktop supports English (`en`), Simplified Chinese (`zh_CN`), Spanish
(`es`), Brazilian Portuguese (`pt_BR`), French (`fr`), Japanese (`ja`) and `system`
preferences. `QSettings("UsageLoop", "UsageLoop")` stores the preference separately
from automation state. The entry point calls `configure_language()` once at
startup. Imports and CLI use stay English and do not access language settings.

Language changes require a full exit and restart, including the tray app.
`system` matches Qt's language/script: Spanish and French regional variants
share their catalogues; Portuguese systems use the available Brazilian variant.
Japanese and Simplified Han select their catalogues. Traditional Han and
unsupported system languages fall back to English. An explicit selection wins.
`USAGELOOP_LANGUAGE=en|zh_CN|es|pt_BR|fr|ja|system` overrides the preference for
previews and development without saving it.

## Changing copy

- Keep English source strings in `tr("English source text")`. Chinese remains
  in `src/sentinel/translations.py`; other catalogues are registered in
  `src/sentinel/locales/__init__.py` and stored as ordinary English-keyed Python
  dictionaries beside it. There are no positional IDs or translation services.
- Update every catalogue when source copy changes. Tests reject missing, empty
  or duplicate entries and mismatched placeholders. Keep newlines and any
  leading/trailing spaces used by existing fragments. Prefer complete sentences
  over new fragments. Static scanning cannot prove dynamic coverage or safely
  identify every obsolete key; do not prune by text search alone.
- For interpolated text, translate the complete template before formatting: `tr("Version {0} is available.").format(version)`. Preserve fields, conversions, and format specifications.
- Translate at presentation boundaries. Never translate protocol identifiers, persistence keys, status values written to provider state, scheduler decisions, model IDs, command lines, URLs, or user-provided release notes.
- Technical diagnostics stay English for maintainers. Unknown strings fall back to English.
- The catalogue is a Python module, so normal source and PyInstaller dependency discovery include it without extra data-file packaging or a translation compiler.

The small `DialogTranslator` handles Qt confirmation buttons and mnemonics.
`ui_locale()` sets widget locales without changing the scheduler's process
locale. English retains AM/PM; the other supported languages use 24-hour controls
and numeric date patterns, avoiding host-locale month/day names. Japanese styling
prefers Yu Gothic UI, Noto Sans CJK JP, Noto Sans JP or Meiryo; Chinese prefers
Microsoft YaHei UI or Noto CJK. The app never downloads fonts. Minimal Linux
desktops need a CJK-capable system font, such as `fonts-noto-cjk` (installed in
Linux CI). Verify Qt's shaped glyph runs including fallbacks, not just the
primary font's character table. Durations retain shared compact `h`/`m` units.

Use `format_datetime()` for displayed dates and times. It keeps the leading
zeroes in all 24-hour times, including `00:00`. Widget object names stay
English and stable; translate the visible label and accessible name instead.

## Validation

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

`test_i18n.py` and `test_languages.py` check all six languages: fallback,
preference persistence across real process restarts, system matching, format
fields, time controls, dialog buttons, status decisions and unchanged scheduler
settings. `test_about_updates.py` covers all languages at six sizes. Tests use
fake providers and temporary settings; they never send Codex requests.

For visual review on Windows, use the native Qt platform so installed fonts are rendered correctly:

```powershell
$env:USAGELOOP_LANGUAGE = 'ja' # or any supported code
$env:QT_QPA_PLATFORM = 'windows'
python scripts/capture-ui-screenshots.py artifacts/preview-ja --language-review
```

The harness uses temporary state/preferences and synthetic data. It never
contacts Codex or GitHub. It captures Dashboard, daily/expanded weekly settings,
About update states, history and the actual Qt confirmation dialog. Keep each
language in its own output folder. `--platform Linux` renders host-specific copy;
it does not prove Linux desktop behavior. Windows offscreen Qt can report no
installed fonts; use native Windows captures and run `test_languages.py` with
`QT_QPA_PLATFORM=windows` for actual text-fit and Japanese glyph checks.

## Review status

The packaged executable also accepts a hidden `--ui-acceptance OUTPUT_FOLDER`
argument for release checks. It opens the actual pages using synthetic data,
temporary settings, a fake updater and no production provider. It returns before
the normal process lock, user settings and startup registration are opened.
Windows installer CI runs it after fresh install and the 1.3.9 upgrade. Linux
package CI runs it under Xvfb with the xcb backend and Noto CJK. Its JSON report
and screenshots are Actions artifacts. This checks packaged widgets, not an
interactive GNOME/KDE session or tray integration.

The four new catalogues are generated text with engineering validation, not
professional or native-reviewed localization. The original Chinese contribution
was generated and tried by its contributor on Windows. Record a native review
only after it happens, naming the language, scope and commit reviewed.

Review uncertain/no-retry states first, then quota/privacy explanations,
schedule examples and midnight times, update confirmation and Linux manual
instructions. Fix wording without changing safety behavior. See the
[validation and screenshot record](1.4.0-multilingual-validation.md).
