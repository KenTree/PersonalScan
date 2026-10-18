"""Shared desktop appearance. Follows the system without storing preferences."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette


def apply_theme(app, scheme=None):
    """Keep native controls and their palette in sync with macOS appearance."""
    dark = (scheme or app.styleHints().colorScheme()) == Qt.ColorScheme.Dark
    colors = dict(
        background="#171d29", surface="#202938", alternate="#252f40",
        text="#e8edf5", muted="#a6b4c9", border="#3b4960",
        accent="#a4bcff", primary="#4263bc", hover="#30415e",
        selection="#354f80", disabled="#7f8da3", error="#ffaca8", progress="#4263bc",
    ) if dark else dict(
        background="#f2f5fa", surface="#ffffff", alternate="#f6f8fc",
        text="#202e46", muted="#53647c", border="#ccd6e5",
        accent="#3155ad", primary="#3155ad", hover="#e9effb",
        selection="#dce7ff", disabled="#6e7e94", error="#ad2938", progress="#b6c9ff",
    )
    palette = QPalette(app.palette())
    for role, key in {
        "Window": "background", "WindowText": "text", "Base": "surface",
        "AlternateBase": "alternate", "Text": "text", "Button": "surface",
        "ButtonText": "text", "Highlight": "selection", "HighlightedText": "text",
        "Link": "accent", "LinkVisited": "accent", "PlaceholderText": "muted",
        "ToolTipBase": "surface", "ToolTipText": "text",
    }.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(colors[key]))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled"]))
    app.setPalette(palette)
    app.setStyleSheet("""
        QMainWindow, QDialog { background: %(background)s; }
        QWidget { font-size: 13px; }
        QLabel { color: %(text)s; }
        QLabel[appearance="title"] { font-size: 28px; font-weight: 700; }
        QLabel[appearance="subtitle"], QLabel[appearance="muted"] { color: %(muted)s; }
        QLabel[appearance="subtitle"] { font-size: 14px; padding-bottom: 6px; }
        QLabel[appearance="status"] {
            color: %(accent)s; background: %(surface)s; border: 1px solid %(border)s;
            border-radius: 8px; padding: 10px 12px;
        }
        QLabel[appearance="error"] { color: %(error)s; }
        QPushButton {
            background: %(surface)s; color: %(text)s; border: 1px solid %(border)s;
            border-radius: 7px; padding: 7px 12px; min-height: 18px;
        }
        QPushButton:hover { background: %(hover)s; border-color: %(accent)s; }
        QPushButton:pressed { background: %(selection)s; }
        QPushButton:default, QPushButton[appearance="primary"] {
            background: %(primary)s; border-color: %(primary)s; color: white; font-weight: 600;
        }
        QPushButton:default:hover, QPushButton[appearance="primary"]:hover { background: #416ac5; }
        QPushButton:focus, QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
            border: 2px solid %(accent)s;
        }
        QPushButton:disabled {
            background: %(alternate)s; color: %(disabled)s; border-color: %(border)s;
        }
        QLineEdit, QComboBox, QSpinBox {
            background: %(surface)s; color: %(text)s; border: 1px solid %(border)s;
            border-radius: 6px; padding: 6px; min-height: 20px;
            selection-background-color: %(selection)s; selection-color: %(text)s;
        }
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled { color: %(disabled)s; }
        QComboBox QAbstractItemView { background: %(surface)s; color: %(text)s; }
        QCheckBox { spacing: 8px; padding: 4px 0; }
        QTreeWidget, QListWidget, QTextBrowser {
            background: %(surface)s; alternate-background-color: %(alternate)s;
            color: %(text)s; border: 1px solid %(border)s; border-radius: 8px;
            selection-background-color: %(selection)s; selection-color: %(text)s;
        }
        QTextBrowser { padding: 12px; }
        QTreeWidget::item, QListWidget::item { padding: 7px 6px; }
        QTreeWidget::item:hover, QListWidget::item:hover { background: %(hover)s; }
        QTreeWidget::item:selected, QListWidget::item:selected {
            background: %(selection)s; color: %(text)s;
        }
        QTreeWidget:focus, QListWidget:focus, QTextBrowser:focus { border-color: %(accent)s; }
        QHeaderView::section {
            background: %(alternate)s; color: %(muted)s; padding: 9px 6px;
            border: none; border-bottom: 1px solid %(border)s; font-weight: 600;
        }
        QTabWidget::pane { border: none; }
        QTabBar::tab {
            background: transparent; color: %(muted)s; padding: 10px 12px;
            border-bottom: 2px solid transparent; margin-bottom: 6px;
        }
        QTabBar::tab:hover { background: %(hover)s; color: %(text)s; }
        QTabBar::tab:selected { color: %(accent)s; border-bottom-color: %(accent)s; font-weight: 600; }
        QProgressBar {
            background: %(alternate)s; color: %(text)s; border: 1px solid %(border)s;
            border-radius: 5px; text-align: center; max-height: 14px; font-size: 10px;
        }
        QProgressBar::chunk { background: %(progress)s; border-radius: 4px; }
        QSplitter::handle { background: %(background)s; height: 7px; }
        QSplitter::handle:hover { background: %(accent)s; }
        QToolTip { background: %(surface)s; color: %(text)s; border: 1px solid %(border)s; padding: 6px; }
    """ % colors)


def install_theme(app):
    apply_theme(app)
    app.styleHints().colorSchemeChanged.connect(lambda scheme: apply_theme(app, scheme))
