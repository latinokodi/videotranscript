"""Design tokens and the stylesheet generated from them (single source of truth)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

APP_NAME = "videotranscript"


ORG_NAME = "videotranscript"


MAX_LOG_LINES = 500


SPACE = {"xs": 4, "sm": 8, "gap": 6, "md": 16, "card": 12, "lg": 24, "xl": 32}


RADIUS = {"sm": 4, "md": 8, "lg": 12}


UI_FONT = '"Segoe UI Variable Display", "Segoe UI", "Noto Sans", sans-serif'


MONO_FONT = '"Cascadia Mono", "Consolas", "JetBrains Mono", monospace'


TYPE = {"eyebrow": 10, "small": 11, "body": 13, "lead": 15, "title": 19}


DARK = {
    "name": "dark",
    "bg": "#0A0A0B",
    "surface": "#121214",
    "surface_alt": "#17171A",
    "surface_hover": "#1D1D21",
    "border": "rgba(255,255,255,0.07)",
    "border_strong": "rgba(255,255,255,0.13)",
    "text": "#E9E9EC",
    "text_muted": "#9B9BA4",
    "text_dim": "#86868F",
    "accent": "#3DDC97",
    "accent_hover": "#4FE6A6",
    "accent_text": "#04231A",
    "danger": "#F87171",
    "warning": "#F5C451",
    "tint_accent": "#15251E",
    "tint_danger": "#2A1919",
    "tint_warning": "#2A2418",
    "tint_neutral": "#1C1C20",
    "selection": "rgba(61,220,151,0.18)",
}


LIGHT = {
    "name": "light",
    "bg": "#F7F6F3",
    "surface": "#FFFFFF",
    "surface_alt": "#FBFBFA",
    "surface_hover": "#F1F1EE",
    "border": "rgba(0,0,0,0.09)",
    "border_strong": "rgba(0,0,0,0.16)",
    "text": "#16181A",
    "text_muted": "#5A5F63",
    "text_dim": "#6E7377",
    "accent": "#0C6F4E",
    "accent_hover": "#0A5E42",
    "accent_text": "#FFFFFF",
    "danger": "#B3261E",
    "warning": "#8A5A00",
    "tint_accent": "#E3F2EA",
    "tint_danger": "#FBE9E7",
    "tint_warning": "#FAF1DA",
    "tint_neutral": "#EEEEEB",
    "selection": "rgba(14,124,87,0.16)",
}


THEMES = {"dark": DARK, "light": LIGHT}


STATUS_STYLE = {
    "Queued": ("tint_neutral", "text_muted"),
    "Running": ("tint_accent", "accent"),
    "Done": ("tint_accent", "accent"),
    # Skipped is deliberately warning-tinted rather than accent-tinted: the row
    # succeeded, but nothing was transcribed, and the colour should not invite the
    # user to read it as fresh work.
    "Skipped": ("tint_warning", "warning"),
    "Failed": ("tint_danger", "danger"),
    "Cancelled": ("tint_warning", "warning"),
}


def build_stylesheet(t: dict) -> str:
    """Generate the QSS from the token table (single source of truth)."""
    return f"""
    * {{
        font-family: {UI_FONT};
        font-size: {TYPE["body"]}px;
        color: {t["text"]};
    }}
    QMainWindow, QDialog, #Root {{ background: {t["bg"]}; }}

    /* ---- cards: outer shell + inner core (double bezel) ---- */
    #Card {{
        background: {t["surface"]};
        border: 1px solid {t["border"]};
        border-radius: {RADIUS["lg"]}px;
    }}
    #CardBody {{ background: transparent; }}
    #Eyebrow {{
        color: {t["text_dim"]};
        font-size: {TYPE["eyebrow"]}px;
        font-weight: 600;
    }}

    /* ---- header ---- */
    #HeaderBar {{
        background: {t["surface"]};
        border: 1px solid {t["border"]};
        border-radius: {RADIUS["lg"]}px;
    }}
    #AppTitle {{ font-size: {TYPE["title"]}px; font-weight: 600; }}
    #AppSubtitle, #HeaderMeta {{ color: {t["text_muted"]}; font-size: {TYPE["small"]}px; }}
    #Chip {{
        background: {t["surface_alt"]};
        border: 1px solid {t["border"]};
        border-radius: {RADIUS["sm"]}px;
        color: {t["text_muted"]};
        font-size: {TYPE["small"]}px;
        padding: 3px 8px;
    }}
    #Chip[accent="true"] {{
        background: {t["tint_accent"]};
        border: 1px solid {t["border"]};
        border-radius: {RADIUS["sm"]}px;
        color: {t["accent"]};
        font-size: {TYPE["small"]}px;
        padding: 3px 8px;
        font-weight: 600;
    }}
    /* ---- inputs ---- */
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: {t["surface_alt"]};
        border: 1px solid {t["border"]};
        border-radius: {RADIUS["md"]}px;
        padding: 6px 10px;
        min-height: 18px;
        selection-background-color: {t["selection"]};
    }}
    QPlainTextEdit {{
        background: {t["surface_alt"]};
        border: 1px solid {t["border"]};
        border-radius: {RADIUS["md"]}px;
        padding: 6px 10px;
        selection-background-color: {t["selection"]};
    }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
    QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
        border-color: {t["border_strong"]};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
    QPlainTextEdit:focus {{
        border: 2px solid {t["accent"]};
        padding: 5px 9px;
    }}
    QLineEdit:disabled, QComboBox:disabled, QPushButton:disabled {{
        color: {t["text_dim"]};
        background: {t["surface"]};
    }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {t["surface_alt"]};
        border: 1px solid {t["border_strong"]};
        border-radius: {RADIUS["sm"]}px;
        selection-background-color: {t["selection"]};
        outline: none;
    }}

    /* ---- buttons ---- */
    QPushButton {{
        background: {t["surface_alt"]};
        border: 1px solid {t["border"]};
        border-radius: {RADIUS["md"]}px;
        padding: 7px 14px;
        font-weight: 500;
    }}
    QPushButton:hover {{ background: {t["surface_hover"]}; border-color: {t["border_strong"]}; }}
    QPushButton:pressed {{ background: {t["surface"]}; }}
    QPushButton:focus {{ border: 2px solid {t["accent"]}; padding: 6px 13px; }}
    QPushButton#Primary {{
        background: {t["accent"]};
        color: {t["accent_text"]};
        border: 1px solid {t["accent"]};
        font-weight: 600;
    }}
    QPushButton#Primary:hover {{ background: {t["accent_hover"]}; }}
    QPushButton#Primary:disabled {{ background: {t["surface_alt"]}; color: {t["text_dim"]};
                                    border-color: {t["border"]}; }}
    QPushButton#Quiet {{ background: transparent; border: 1px solid transparent;
                         color: {t["text_muted"]}; }}
    QPushButton#Quiet:hover {{ background: {t["surface_hover"]}; color: {t["text"]}; }}
    QToolButton {{ border: none; padding: 4px; border-radius: {RADIUS["sm"]}px; }}
    QToolButton:hover {{ background: {t["surface_hover"]}; }}

    QCheckBox, QRadioButton {{ spacing: 8px; padding: 2px 0; }}
    QCheckBox::indicator, QRadioButton::indicator {{ width: 15px; height: 15px; }}
    QCheckBox::indicator {{ border: 1px solid {t["border_strong"]}; border-radius: 4px;
                            background: {t["surface_alt"]}; }}
    QCheckBox::indicator:checked {{ background: {t["accent"]}; border-color: {t["accent"]}; }}
    QRadioButton::indicator {{ border: 1px solid {t["border_strong"]}; border-radius: 8px;
                               background: {t["surface_alt"]}; }}
    QRadioButton::indicator:checked {{ background: {t["accent"]}; border: 4px solid {t["surface_alt"]};
                                       outline: 1px solid {t["accent"]}; }}

    /* ---- table ---- */
    QTableView {{
        background: {t["surface"]};
        alternate-background-color: {t["surface_alt"]};
        border: 1px solid {t["border"]};
        border-radius: {RADIUS["md"]}px;
        gridline-color: transparent;
        selection-background-color: {t["selection"]};
        selection-color: {t["text"]};
        outline: none;
    }}
    QTableView::item {{ padding: 6px 8px; border: none; }}
    QHeaderView::section {{
        background: {t["surface"]};
        color: {t["text_dim"]};
        border: none;
        border-bottom: 1px solid {t["border"]};
        padding: 8px;
        font-size: {TYPE["small"]}px;
        font-weight: 600;
    }}
    QTableView QTableCornerButton::section {{ background: {t["surface"]}; border: none; }}

    /* ---- progress (tall enough to show its percentage) ---- */
    QProgressBar {{
        background: {t["surface_alt"]};
        border: 1px solid {t["border"]};
        border-radius: 6px;
        min-height: 17px;
        max-height: 17px;
        color: {t["text"]};
        font-size: {TYPE["small"]}px;
        font-weight: 600;
        text-align: center;
    }}
    QProgressBar::chunk {{ background: {t["accent"]}; border-radius: 3px; }}

    /* ---- misc chrome ---- */
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {t["border_strong"]}; border-radius: 4px;
                                   min-height: 28px; }}
    QScrollBar::handle:vertical:hover {{ background: {t["text_dim"]}; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {t["border_strong"]}; border-radius: 4px;
                                     min-width: 28px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QSplitter::handle {{ background: transparent; width: {SPACE["sm"]}px; }}

    #FooterBar {{
        background: {t["surface"]};
        border: 1px solid {t["border"]};
        border-radius: {RADIUS["lg"]}px;
    }}
    #EmptyTitle {{ font-size: {TYPE["lead"]}px; font-weight: 600; }}
    #EmptyHint {{ color: {t["text_muted"]}; font-size: {TYPE["small"]}px; }}
    #Meta {{ color: {t["text_muted"]}; font-size: {TYPE["small"]}; }}
    #MetaMono {{ color: {t["text_muted"]}; font-family: {MONO_FONT}; font-size: {TYPE["small"]}; }}

    QMenuBar {{ background: {t["surface"]}; }}
    QMenuBar::item {{ padding: 6px 10px; background: transparent; border-radius: {RADIUS["sm"]}px; }}
    QMenuBar::item:selected {{ background: {t["surface_hover"]}; }}
    QMenu {{ background: {t["surface_alt"]}; border: 1px solid {t["border_strong"]};
             border-radius: {RADIUS["md"]}px; padding: 6px; }}
    QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: {RADIUS["sm"]}px; }}
    QMenu::item:selected {{ background: {t["selection"]}; }}
    QMenu::separator {{ height: 1px; background: {t["border"]}; margin: 6px 8px; }}
    QStatusBar {{ background: {t["surface"]}; color: {t["text_muted"]}; }}
    QStatusBar::item {{ border: none; }}
    QToolTip {{
        background: {t["surface_alt"]};
        color: {t["text"]};
        border: 1px solid {t["border_strong"]};
        border-radius: {RADIUS["sm"]}px;
        padding: 4px 8px;
    }}
    """


def make_app_icon(t: dict) -> QIcon:
    """Draw the mark in code - no image assets to ship."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(t["surface_alt"]))
    painter.drawRoundedRect(1, 1, 62, 62, 14, 14)
    painter.setBrush(QColor(t["accent"]))
    for i, height in enumerate((16, 30, 42, 26, 34, 18)):
        x = 12 + i * 7
        painter.drawRoundedRect(x, 32 - height // 2, 4, height, 2, 2)
    painter.end()
    return QIcon(pixmap)
