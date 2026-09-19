"""Shared Qt palette and styles for windows, dialogs and menus."""
from PyQt6.QtGui import QColor, QPalette


def apply_theme(app, dark=False):
    colors = dict(bg="#101722", panel="#182230", field="#202d3e", line="#314156",
                  text="#e7edf5", muted="#a4b3c6", accent="#78aaff", selected="#263f62",
                  hover="#293b50", danger="#ff919b") if dark else dict(
                  bg="#f3f6fa", panel="#ffffff", field="#f7f9fc", line="#dbe3ee",
                  text="#1a2b42", muted="#5e7088", accent="#255fd4", selected="#e4edff",
                  hover="#edf2fa", danger="#b93649")
    c = colors
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in ((QPalette.ColorRole.Window, c["bg"]), (QPalette.ColorRole.WindowText, c["text"]),
                        (QPalette.ColorRole.Base, c["panel"]), (QPalette.ColorRole.AlternateBase, c["field"]),
                        (QPalette.ColorRole.Text, c["text"]), (QPalette.ColorRole.Button, c["panel"]),
                        (QPalette.ColorRole.ButtonText, c["text"]), (QPalette.ColorRole.Highlight, c["selected"]),
                        (QPalette.ColorRole.HighlightedText, c["text"]),
                        (QPalette.ColorRole.PlaceholderText, c["muted"]),
                        (QPalette.ColorRole.ToolTipBase, c["panel"]), (QPalette.ColorRole.ToolTipText, c["text"])):
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(c["muted"]))
    app.setPalette(palette)
    app.setStyleSheet("""
        QWidget { font-family: 'Segoe UI'; font-size: 13px; color: %(text)s; }
        QMainWindow, QDialog, #ContentArea { background: %(bg)s; }
        QLabel { background: transparent; }
        #Sidebar, #DashBox, #EmptyState { background: %(panel)s; border: 1px solid %(line)s; border-radius: 10px; }
        #Sidebar { border-radius: 0; border-top: none; border-left: none; border-bottom: none; }
        #Logo { font-size: 21px; font-weight: 700; color: %(accent)s; }
        #PageTitle { font-size: 26px; font-weight: 700; }
        #DialogTitle { font-size: 21px; font-weight: 600; }
        #Muted, #SectionHeader, #DashTitle { color: %(muted)s; }
        #SectionHeader { font-size: 11px; font-weight: 600; }
        #DashValue { font-size: 18px; font-weight: 600; }
        #CardValue { font-size: 27px; font-weight: 600; }
        #SelectionCount { color: %(accent)s; font-weight: 600; }
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox { background: %(panel)s; border: 1px solid %(line)s;
            border-radius: 6px; padding: 7px; selection-background-color: %(selected)s; }
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: %(accent)s; }
        QPushButton { background: %(panel)s; border: 1px solid %(line)s; border-radius: 6px;
            padding: 8px 12px; min-height: 16px; }
        QPushButton:hover { background: %(hover)s; border-color: %(accent)s; }
        QPushButton:pressed, QPushButton:checked { background: %(selected)s; }
        QPushButton:focus { border-color: %(accent)s; }
        #RangeSectionTitle { font-size: 14px; font-weight: 600; }
        #RangePreview { background: %(panel)s; border: 1px solid %(line)s; border-radius: 8px; }
        #RangeSummary { color: %(accent)s; font-size: 19px; font-weight: 600; }
        #ValidationError { color: %(danger)s; }
        #RangeEditor { font-family: 'Consolas'; font-size: 14px; }
        #NetworkList::item { padding: 10px 7px; }
        QPushButton[primary="true"]:disabled { background: %(field)s; color: %(muted)s; border-color: %(line)s; }
        #RangeAction { padding: 8px 4px; }
        QPushButton:disabled { color: %(muted)s; background: %(field)s; border-color: %(line)s; }
        #BtnScan, QPushButton[primary="true"] { background: #2864dc; color: white; border-color: #2864dc; font-weight: 600; }
        #BtnScan:hover, QPushButton[primary="true"]:hover { background: #1d52be; }
        #BtnScan:disabled { background: %(field)s; color: %(muted)s; border-color: %(line)s; }
        #BtnStop { color: %(danger)s; }
        QCheckBox, QRadioButton { spacing: 8px; padding: 4px 0; }
        QCheckBox::indicator { width: 16px; height: 16px; }
        QListWidget, QTableWidget { background: %(panel)s; alternate-background-color: %(field)s;
            border: 1px solid %(line)s; border-radius: 8px; outline: none; }
        QListWidget::item { padding: 9px 7px; border-radius: 4px; }
        QListWidget::item:selected, QTableWidget::item:selected { background: %(selected)s; color: %(text)s; }
        QTableWidget::item { padding: 4px 8px; border-bottom: 1px solid %(line)s; }
        QHeaderView::section { background: %(field)s; color: %(muted)s; padding: 11px 8px;
            border: none; border-bottom: 1px solid %(line)s; font-weight: 600; }
        QScrollArea, QStackedWidget { border: none; background: transparent; }
        QScrollArea > QWidget > QWidget { background: transparent; }
        #SidebarScroll, #SidebarScroll > QWidget > QWidget { background: %(panel)s; }
        #SettingsNavigation { border: none; background: transparent; }
        #SettingsNavigation::item { padding: 12px 10px; }
        QGroupBox { background: %(panel)s; border: 1px solid %(line)s; border-radius: 8px; margin-top: 16px; padding: 14px; }
        QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 5px; font-weight: 600; }
        QProgressBar { background: %(line)s; border: none; border-radius: 3px; }
        QProgressBar::chunk { background: #2864dc; border-radius: 3px; }
        QMenuBar { background: %(panel)s; border-bottom: 1px solid %(line)s; }
        QMenuBar::item { padding: 6px 12px; background: transparent; }
        QMenuBar::item:selected { background: %(selected)s; }
        QMenu { background: %(panel)s; border: 1px solid %(line)s; padding: 5px; }
        QMenu::item { padding: 8px 24px; border-radius: 4px; }
        QMenu::item:selected { background: %(selected)s; }
        QMenu::item:disabled { color: %(muted)s; }
        QToolTip { background: %(panel)s; color: %(text)s; border: 1px solid %(line)s; padding: 6px; }
        QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
        QScrollBar::handle:vertical { background: %(line)s; border-radius: 5px; min-height: 24px; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    """ % colors)
