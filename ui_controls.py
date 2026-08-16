from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent, QPainter
from PySide6.QtWidgets import QHeaderView, QSpinBox, QStyle, QStyleOptionButton, QTableWidget


class ProxyDropTable(QTableWidget):
    proxy_file_dropped = Signal(str)

    def __init__(self, rows: int, columns: int, parent=None):
        super().__init__(rows, columns, parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._first_proxy_path(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        path = self._first_proxy_path(event.mimeData())
        if path:
            self.proxy_file_dropped.emit(path)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    @staticmethod
    def _first_proxy_path(mime_data) -> str:
        for url in mime_data.urls() if mime_data.hasUrls() else ():
            path = url.toLocalFile()
            if path.lower().endswith(".txt"):
                return path
        return ""


class CheckBoxHeader(QHeaderView):
    toggle_all_requested = Signal(bool)

    def __init__(self, orientation: Qt.Orientation, parent=None):
        super().__init__(orientation, parent)
        self._check_state = Qt.CheckState.Checked
        self._checkbox_enabled = True

    def set_check_state(self, state: Qt.CheckState) -> None:
        if self._check_state == state:
            return
        self._check_state = state
        self.updateSection(0)

    def set_checkbox_enabled(self, enabled: bool) -> None:
        self._checkbox_enabled = enabled
        self.updateSection(0)

    def paintSection(self, painter: QPainter, rect: QRect, logical_index: int) -> None:
        super().paintSection(painter, rect, logical_index)
        if logical_index != 0:
            return
        option = QStyleOptionButton()
        indicator = self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator, option, self
        )
        option.rect = QRect(
            rect.x() + (rect.width() - indicator.width()) // 2,
            rect.y() + (rect.height() - indicator.height()) // 2,
            indicator.width(),
            indicator.height(),
        )
        option.state = QStyle.StateFlag.State_On
        if self._check_state == Qt.CheckState.Unchecked:
            option.state = QStyle.StateFlag.State_Off
        elif self._check_state == Qt.CheckState.PartiallyChecked:
            option.state = QStyle.StateFlag.State_NoChange
        if self._checkbox_enabled:
            option.state |= QStyle.StateFlag.State_Enabled
        self.style().drawControl(QStyle.ControlElement.CE_CheckBox, option, painter, self)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._checkbox_enabled and self.logicalIndexAt(event.position().toPoint()) == 0:
            checked = self._check_state != Qt.CheckState.Checked
            self.set_check_state(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
            self.toggle_all_requested.emit(checked)
            event.accept()
            return
        super().mousePressEvent(event)


def make_spin(minimum: int, maximum: int, value: int, suffix: str) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    spin.setSuffix(suffix)
    return spin
