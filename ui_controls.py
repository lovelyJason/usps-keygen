from PySide6.QtWidgets import QSpinBox


def make_spin(minimum: int, maximum: int, value: int, suffix: str) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    spin.setSuffix(suffix)
    return spin
