DARK_STYLE = """
QMainWindow, QDialog, QWidget {
    background-color: #0D1211;
    color: #E8EFEC;
    font-family: 'Inter', 'Avenir Next', 'Segoe UI', sans-serif;
}
QSplitter::handle { background-color: #1F2A28; width: 1px; }
QScrollBar:vertical { border: none; background: transparent; width: 10px; margin: 4px 2px; }
QScrollBar::handle:vertical { background: #2E3E39; min-height: 30px; border-radius: 5px; }
QScrollBar::handle:vertical:hover { background: #4A605A; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { height: 0px; background: transparent; }
QTextEdit, QPlainTextEdit {
    background-color: #131A19;
    border: 1px solid #1F2A28;
    border-radius: 10px;
    color: #E8EFEC;
    padding: 12px;
    selection-background-color: #7EE0A8;
    selection-color: #0D1211;
}
QTextEdit#ChatView { background-color: #0F1514; border: none; padding: 20px 28px; font-size: 14px; }
QPlainTextEdit#ChatInput { background-color: #161E1D; border: 1px solid #27332F; border-radius: 12px; padding: 12px 16px; font-size: 14px; }
QPlainTextEdit#ChatInput:focus { border: 1px solid #4A7A5E; }
QComboBox { background-color: #161E1D; border: 1px solid #27332F; border-radius: 8px; color: #E8EFEC; padding: 8px 12px; min-height: 20px; }
QComboBox:hover { border: 1px solid #3A4A45; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView { background-color: #1A2423; color: #E8EFEC; selection-background-color: #2E5A44; selection-color: #F5FFF7; border: 1px solid #33423E; }
QPushButton { background-color: #7EE0A8; color: #0D2018; font-weight: 600; border: 1px solid #7EE0A8; border-radius: 8px; padding: 8px 14px; min-height: 20px; font-size: 12px; }
QPushButton:hover { background-color: #99EDBD; border-color: #99EDBD; }
QPushButton:pressed { background-color: #5FC48B; border-color: #5FC48B; }
QPushButton:disabled, QComboBox:disabled { background-color: #1A2220; color: #5E6F69; border-color: #232D2B; }
QWidget#Sidebar { background-color: #131A19; border-right: 1px solid #1F2A28; }
QLabel#AppTitle { color: #F2F7F3; font-size: 20px; font-weight: 700; letter-spacing: 1px; padding: 4px 0 10px; background-color: transparent; }
QLabel#SectionTitle { color: #6E8079; font-size: 10px; font-weight: 700; letter-spacing: 1.2px; padding: 10px 0 4px; background-color: transparent; }
QLabel#FolderLabel { color: #A9B9B0; background: transparent; background-color: rgba(0, 0, 0, 0); border: none; padding: 2px 0 8px; }
QLabel#FolderLabel:focus { background: transparent; background-color: rgba(0, 0, 0, 0); }
QPushButton#SecondaryButton { background-color: #17201E; color: #C7D6CF; border: 1px solid #27332F; border-radius: 8px; padding: 8px 12px; text-align: left; font-weight: 600; font-size: 11px; min-height: 16px; }
QPushButton#SecondaryButton:hover { background-color: #1F2E2A; border-color: #3A4A45; color: #E8EFEC; }
QPushButton#SecondaryButton:pressed { background-color: #131C1A; }
QPushButton#SecondaryButton:disabled { background-color: #141B1A; color: #5E6F69; border-color: #232D2B; }
QStatusBar { background-color: #131A19; color: #8FA79A; border-top: 1px solid #1F2A28; }
QLabel#StatusText { color: #8FA79A; padding-left: 4px; }
QLabel#ElapsedIndicator { color: #7EE0A8; font-size: 11px; padding: 0 12px; }
QLabel#ThinkingLabel { color: #7EE0A8; font-size: 15px; font-weight: 600; padding: 0 4px 2px; }
QWidget#ChatArea { background-color: #0D1211; }
"""
