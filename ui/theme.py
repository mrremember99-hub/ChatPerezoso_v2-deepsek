DARK_STYLE = """
QMainWindow, QDialog, QWidget {
    background-color: #0A0F0E;
    color: #EAF1EE;
    font-family: 'Inter', 'Avenir Next', 'Segoe UI', sans-serif;
    font-size: 13px;
}
QSplitter::handle { background-color: #1A2523; width: 1px; }

QScrollBar:vertical {
    border: none; background: transparent; width: 10px; margin: 4px 2px;
}
QScrollBar::handle:vertical {
    background: #26332F; min-height: 30px; border-radius: 5px;
}
QScrollBar::handle:vertical:hover { background: #3A4A45; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0px; background: transparent;
}

QTextEdit, QPlainTextEdit {
    background-color: #0F1614;
    border: 1px solid #1A2523;
    border-radius: 10px;
    color: #EAF1EE;
    padding: 12px;
    selection-background-color: #7EE0A8;
    selection-color: #0A0F0E;
}
QTextEdit#ChatView {
    background-color: #0C1211;
    border: none;
    padding: 24px 32px;
    font-size: 14px;
}
QPlainTextEdit#ChatInput {
    background-color: #111A18;
    border: 1px solid #26332F;
    border-radius: 12px;
    padding: 12px 16px;
    font-size: 14px;
}
QPlainTextEdit#ChatInput:focus { border: 1px solid #3A6B52; }

QComboBox {
    background-color: #111A18;
    border: 1px solid #26332F;
    border-radius: 8px;
    color: #EAF1EE;
    padding: 8px 12px;
    min-height: 20px;
}
QComboBox:hover { border: 1px solid #3A4A45; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView {
    background-color: #141E1C;
    color: #EAF1EE;
    selection-background-color: #2E5A44;
    selection-color: #F5FFF7;
    border: 1px solid #26332F;
}

QPushButton {
    background-color: #7EE0A8;
    color: #0A1F15;
    font-weight: 600;
    border: 1px solid #7EE0A8;
    border-radius: 8px;
    padding: 8px 14px;
    min-height: 20px;
    font-size: 12px;
}
QPushButton:hover { background-color: #99EDBD; border-color: #99EDBD; }
QPushButton:pressed { background-color: #5FC48B; border-color: #5FC48B; }
QPushButton:disabled, QComboBox:disabled {
    background-color: #141B1A;
    color: #5D6E68;
    border-color: #1A2523;
}

QWidget#Sidebar {
    background-color: #0F1614;
    border-right: 1px solid #1A2523;
}
QLabel#AppTitle {
    color: #F2F7F3;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 1.5px;
    padding: 4px 0 12px;
    background-color: transparent;
}
QLabel#SectionTitle {
    color: #5D6E68;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.4px;
    padding: 12px 0 4px;
    background-color: transparent;
}
QLabel#FolderLabel {
    color: #9BAFA7;
    background: transparent;
    background-color: rgba(0, 0, 0, 0);
    border: none;
    padding: 2px 0 8px;
}
QLabel#FolderLabel:focus {
    background: transparent;
    background-color: rgba(0, 0, 0, 0);
}

QPushButton#SecondaryButton {
    background-color: #111A18;
    color: #C7D6CF;
    border: 1px solid #26332F;
    border-radius: 8px;
    padding: 8px 12px;
    text-align: left;
    font-weight: 600;
    font-size: 11px;
    min-height: 16px;
}
QPushButton#SecondaryButton:hover {
    background-color: #17211F;
    border-color: #3A4A45;
    color: #EAF1EE;
}
QPushButton#SecondaryButton:pressed { background-color: #0D1413; }
QPushButton#SecondaryButton:disabled {
    background-color: #0E1413;
    color: #5D6E68;
    border-color: #1A2523;
}

QPushButton#McpToggle {
    background-color: #111A18;
    color: #9BAFA7;
    border: 1px solid #26332F;
    border-radius: 10px;
    padding: 8px 14px;
    text-align: left;
    font-weight: 600;
    font-size: 11px;
    min-height: 20px;
}
QPushButton#McpToggle:hover {
    border-color: #3A4A45;
    color: #EAF1EE;
}
QPushButton#McpToggle:checked {
    background-color: #17301F;
    color: #7EE0A8;
    border-color: #3A6B52;
}
QPushButton#McpToggle:checked:hover {
    background-color: #1B3A26;
    border-color: #4A7A5E;
}
QPushButton#McpToggle:disabled {
    background-color: #0E1413;
    color: #5D6E68;
    border-color: #1A2523;
}

QPushButton#McpToggle {
    background-color: #111A18;
    color: #9BAFA7;
    border: 1px solid #26332F;
    border-radius: 8px;
    padding: 8px 14px;
    text-align: left;
    font-weight: 600;
    font-size: 12px;
    min-height: 22px;
}
QPushButton#McpToggle:hover {
    border-color: #3A4A45;
    color: #EAF1EE;
}
QPushButton#McpToggle:checked {
    background-color: #17301F;
    color: #7EE0A8;
    border: 1px solid #3A6B52;
}
QPushButton#McpToggle:checked:hover {
    background-color: #1B3A26;
    border-color: #4A7A5E;
}
QPushButton#McpToggle:disabled {
    background-color: #0E1413;
    color: #5D6E68;
    border-color: #1A2523;
}

QStatusBar {
    background-color: #0F1614;
    color: #8FA79A;
    border-top: 1px solid #1A2523;
}
QLabel#StatusText { color: #8FA79A; padding-left: 4px; }

QLabel#ElapsedIndicator {
    color: #7EE0A8;
    font-size: 11px;
    padding: 0 12px;
}
QLabel#ThinkingLabel {
    color: #7EE0A8;
    font-size: 15px;
    font-weight: 600;
    padding: 0 4px 2px;
}
QWidget#ChatArea { background-color: #0A0F0E; }

QLabel#DiagnosticLine {
    color: #8FA79A;
    font-size: 11px;
    padding: 1px 0;
    background-color: transparent;
}
"""

DOCUMENT_STYLESHEET = """
body { color: #DDE5E1; }
h1 { font-size: 1.4em; color: #EAF1EE; margin: 12px 0 6px 0; }
h2 { font-size: 1.2em; color: #EAF1EE; margin: 10px 0 4px 0; }
h3 { font-size: 1.1em; color: #EAF1EE; margin: 8px 0 4px 0; }
h4, h5, h6 { color: #EAF1EE; margin: 6px 0 4px 0; }
p { margin: 4px 0; }
a { color: #7EE0A8; }
strong { font-weight: bold; }
code { font-family: Menlo; font-size: 9pt; color: #A8BAB3; }
pre {
    font-family: Menlo;
    font-size: 9pt;
    color: #A8BAB3;
    background-color: #131B1A;
    padding: 8px 10px;
    border-radius: 6px;
}
blockquote {
    border-left: 3px solid #3A6B52;
    padding-left: 10px;
    color: #9BAFA7;
    margin: 6px 0;
}
ul, ol { margin: 4px 0; padding-left: 20px; }
li { margin: 2px 0; }
hr { border: none; border-top: 1px solid #26332F; margin: 10px 0; }
table { border-collapse: collapse; margin: 8px 0; }
th {
    border: 1px solid #26332F;
    padding: 4px 8px;
    background-color: #141E1C;
    color: #EAF1EE;
    font-weight: bold;
}
td { border: 1px solid #26332F; padding: 4px 8px; }
"""
