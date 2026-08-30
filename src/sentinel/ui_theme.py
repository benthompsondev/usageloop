"""Central design tokens and Qt styling for the desktop shell."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemeTokens:
    background: str = "#0A111C"
    background_deep: str = "#070D16"
    surface: str = "#101B29"
    surface_sunken: str = "#0C1522"
    surface_raised: str = "#16222F"
    surface_hover: str = "#1B283A"
    border: str = "#253449"
    border_strong: str = "#33465E"
    text: str = "#EDF3FA"
    text_soft: str = "#C2CEDC"
    text_muted: str = "#8B99AA"
    text_faint: str = "#6B7A8C"
    accent: str = "#34D399"
    accent_deep: str = "#146B3F"
    accent_hover: str = "#1A8A4E"
    accent_ink: str = "#04150E"
    info: str = "#74C4FF"
    warning: str = "#F1D38A"
    error: str = "#FFB4AE"
    radius_small: int = 8
    radius: int = 12
    radius_large: int = 16
    space_1: int = 4
    space_2: int = 8
    space_3: int = 12
    space_4: int = 16
    space_5: int = 24
    space_6: int = 32
    font_family: str = "Segoe UI Variable, Segoe UI"


TOKENS = ThemeTokens()


def desktop_stylesheet(tokens: ThemeTokens = TOKENS) -> str:
    """Return the one app-wide style sheet built from semantic tokens."""

    return f"""
    QWidget {{
        color: {tokens.text};
        font-family: "Segoe UI Variable", "Segoe UI";
        font-size: 14px;
    }}
    QMainWindow, QWidget#appRoot, QScrollArea, QScrollArea > QWidget > QWidget {{
        background: {tokens.background};
    }}
    QScrollArea {{ border: 0; }}
    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {tokens.border_strong}; border-radius: 5px; min-height: 32px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QFrame#appHeader {{
        background: {tokens.background_deep};
        border-bottom: 1px solid {tokens.border};
    }}
    QLabel#brandMark {{
        background: {tokens.accent}; color: {tokens.accent_ink};
        border-radius: 11px; font-size: 15px; font-weight: 800;
    }}
    QLabel#appName {{ font-size: 19px; font-weight: 750; color: {tokens.text}; }}
    QLabel#appPurpose, QLabel#muted, QLabel[muted="true"] {{ color: {tokens.text_muted}; }}
    QPushButton#navButton {{
        background: transparent; border: 1px solid transparent;
        border-radius: {tokens.radius_small}px; color: {tokens.text_muted};
        padding: 8px 13px; font-weight: 650;
    }}
    QPushButton#navButton:hover {{ background: {tokens.surface}; color: {tokens.text}; }}
    QPushButton#navButton:focus {{ border: 1px solid {tokens.accent}; }}
    QPushButton#navButton[active="true"] {{
        background: {tokens.surface_raised}; color: {tokens.accent};
        border: 1px solid {tokens.border_strong};
    }}
    QLabel#pageTitle {{ font-size: 27px; font-weight: 760; color: {tokens.text}; }}
    QLabel#pageIntro {{ font-size: 14px; color: {tokens.text_muted}; }}
    QFrame#surfaceCard, QFrame#providerCard, QFrame#primaryControl {{
        background: {tokens.surface};
        border: 1px solid {tokens.border};
        border-radius: {tokens.radius}px;
    }}
    QFrame#primaryControl {{
        background: {tokens.surface_raised};
        border: 1px solid {tokens.accent_deep};
    }}
    QLabel#eyebrow {{
        color: {tokens.accent}; font-size: 11px; font-weight: 800;
    }}
    QLabel#sectionTitle {{ font-size: 17px; font-weight: 720; color: {tokens.text}; }}
    QLabel#providerName {{ font-size: 18px; font-weight: 740; color: {tokens.text}; }}
    QLabel#countdown {{ font-size: 29px; font-weight: 780; color: {tokens.text}; }}
    QLabel#secondaryMetric {{ color: {tokens.text_soft}; font-size: 13px; }}
    QLabel#detail {{ color: {tokens.text_muted}; line-height: 1.4; }}
    QLabel#statusPill {{
        border-radius: 10px; padding: 4px 9px; font-size: 10px; font-weight: 800;
    }}
    QLabel#statusPill[tone="success"] {{
        color: {tokens.accent}; background: #34D3991C; border: 1px solid #34D39955;
    }}
    QLabel#statusPill[tone="warning"] {{
        color: {tokens.warning}; background: #E3B34118; border: 1px solid #E3B34155;
    }}
    QLabel#statusPill[tone="error"] {{
        color: {tokens.error}; background: #F8514918; border: 1px solid #F8514955;
    }}
    QLabel#statusPill[tone="info"] {{
        color: {tokens.info}; background: #1E9FFF18; border: 1px solid #1E9FFF55;
    }}
    QLabel#statusPill[tone="neutral"] {{
        color: {tokens.text_muted}; background: {tokens.surface_sunken};
        border: 1px solid {tokens.border};
    }}
    QCheckBox#automationToggle {{
        spacing: 13px; font-size: 17px; font-weight: 720; color: {tokens.text};
    }}
    QCheckBox::indicator {{
        width: 38px; height: 22px; border-radius: 11px;
        background: {tokens.surface_sunken}; border: 1px solid {tokens.border_strong};
    }}
    QCheckBox::indicator:hover {{ border: 1px solid {tokens.text_muted}; }}
    QCheckBox::indicator:checked {{
        background: {tokens.accent}; border: 1px solid {tokens.accent};
    }}
    QCheckBox:focus {{ color: {tokens.accent}; }}
    QPushButton {{
        background: {tokens.surface_raised}; color: {tokens.text_soft};
        border: 1px solid {tokens.border_strong}; border-radius: {tokens.radius_small}px;
        padding: 9px 14px; font-weight: 650;
    }}
    QPushButton:hover {{ background: {tokens.surface_hover}; color: {tokens.text}; }}
    QPushButton:focus {{ border: 1px solid {tokens.accent}; }}
    QPushButton:disabled {{ color: {tokens.text_faint}; background: {tokens.surface_sunken}; }}
    QPushButton#primaryButton {{
        background: {tokens.accent_deep}; color: #E8FFF0; border: 1px solid {tokens.accent};
    }}
    QPushButton#primaryButton:hover {{ background: {tokens.accent_hover}; }}
    QPushButton#linkButton {{
        background: transparent; color: {tokens.info}; border: 1px solid {tokens.border};
    }}
    QFrame#settingRow {{
        background: {tokens.surface_sunken}; border: 1px solid {tokens.border};
        border-radius: {tokens.radius_small}px;
    }}
    QLabel#diagnosticValue {{ color: {tokens.text_soft}; font-family: "Cascadia Mono", Consolas; }}
    QLabel#updateState[status="success"] {{ color: {tokens.accent}; }}
    QLabel#updateState[status="error"] {{ color: {tokens.error}; }}
    QLabel#updateState[status="warning"] {{ color: {tokens.warning}; }}
    QToolTip {{
        background: {tokens.surface_raised}; color: {tokens.text};
        border: 1px solid {tokens.border_strong}; padding: 5px;
    }}
    QMessageBox {{ background: {tokens.background}; }}
    """
