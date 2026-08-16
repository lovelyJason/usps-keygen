APP_STYLE = """
QMainWindow { background: #f4f5f6; }
QWidget { font-family: "Segoe UI", "Microsoft YaHei"; font-size: 13px; color: #20262d; }
QLabel#pageTitle { font-size: 22px; font-weight: 700; color: #233e52; }
QGroupBox {
    background: white; border: 1px solid #cfd5da;
    margin-top: 10px; padding: 10px;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 10px;
    padding: 0 4px; font-weight: 600;
}
QPushButton {
    min-height: 30px; padding: 0 12px;
    border: 1px solid #aeb7bf; background: #ffffff;
}
QPushButton:hover { background: #edf2f5; }
QPushButton#primaryButton {
    color: white; background: #1976a3;
    border-color: #125d80; font-weight: 600;
}
QPushButton:disabled {
    color: #8a939b; background: #eceff1; border-color: #c8ced3;
}
QLineEdit, QSpinBox, QTextEdit, QTableWidget {
    background: white; border: 1px solid #bfc7ce;
}
QLineEdit, QSpinBox { min-height: 29px; padding: 0 6px; }
QHeaderView::section {
    background: #e8ecef; padding: 7px; border: 0;
    border-right: 1px solid #cbd1d6; font-weight: 600;
}
"""
