import sys

import numpy as np
from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QListWidget,
    QMainWindow, QPushButton, QVBoxLayout, QWidget,
)

from cyndilib import VideoFrameSync
from cyndilib.finder import Finder
from cyndilib.receiver import Receiver
from cyndilib.wrapper.ndi_recv import RecvColorFormat


class ReceiveThread(QThread):
    frame_ready = pyqtSignal(np.ndarray, int, int)

    def __init__(self, receiver: Receiver):
        super().__init__()
        self._receiver = receiver
        self._running = True

    def run(self):
        vf = VideoFrameSync()
        fs = self._receiver.frame_sync
        fs.set_video_frame(vf)

        while self._running:
            fs.capture_video()
            w, h = vf.xres, vf.yres
            if w > 0 and h > 0:
                self.frame_ready.emit(vf.get_array(), w, h)

    def stop(self):
        self._running = False
        self._receiver.disconnect()  # unblocks capture_video()
        self.wait()


class NDIViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NDI Viewer")
        self.thread: ReceiveThread | None = None

        self.source_list = QListWidget()
        self.source_list.itemSelectionChanged.connect(self._on_selection_changed)

        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.hide()

        self.btn_start = QPushButton("Start Stream")
        self.btn_start.setEnabled(False)
        self.btn_start.clicked.connect(self._start_stream)

        self.btn_stop = QPushButton("Stop Stream")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_stream)

        btn_close = QPushButton("Exit")
        btn_close.clicked.connect(self.close)
        btn_close.setFixedWidth(60)
        btn_close.setStyleSheet("QPushButton { background-color: #c0392b; color: white; border-radius: 4px; padding: 4px; }"
                                "QPushButton:hover { background-color: #e74c3c; }")

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_stop)
        btn_layout.addWidget(btn_close)

        layout = QVBoxLayout()
        layout.addWidget(self.source_list)
        layout.addWidget(self.label)
        layout.addLayout(btn_layout)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.finder = Finder()
        self.finder.open()

        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self._refresh_sources)
        self.refresh_timer.start(1000)
        self._refresh_sources()

    def _refresh_sources(self):
        names = self.finder.get_source_names()
        selected = self.source_list.currentItem()
        selected_name = selected.text() if selected else None

        self.source_list.clear()
        self.source_list.addItems(names)

        if selected_name:
            matches = self.source_list.findItems(selected_name, Qt.MatchExactly)
            if matches:
                self.source_list.setCurrentItem(matches[0])

    def _on_selection_changed(self):
        self.btn_start.setEnabled(bool(self.source_list.selectedItems()) and self.thread is None)

    def _start_stream(self):
        item = self.source_list.currentItem()
        if item is None:
            return

        source = self.finder.get_source(item.text())
        self.source_list.hide()
        self.label.show()
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.setWindowTitle(f"NDI: {item.text()}")

        self.receiver = Receiver(color_format=RecvColorFormat.BGRX_BGRA)
        self.receiver.set_source(source)

        self.thread = ReceiveThread(self.receiver)
        self.thread.frame_ready.connect(self._on_frame)
        self.thread.start()

    def _stop_stream(self):
        if self.thread is not None:
            self.thread.stop()
            self.thread = None
        self.label.hide()
        self.label.clear()
        self.source_list.show()
        self.btn_stop.setEnabled(False)
        self.btn_start.setEnabled(bool(self.source_list.selectedItems()))
        self.setWindowTitle("NDI Viewer")

    def _on_frame(self, data: np.ndarray, w: int, h: int):
        if self.label.width() != w or self.label.height() != h:
            self.resize(w, h)
        img = QImage(data.data, w, h, w * 4, QImage.Format_ARGB32)
        self.label.setPixmap(QPixmap.fromImage(img))

    def closeEvent(self, event):
        self._stop_stream()
        self.refresh_timer.stop()
        self.finder.close()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = NDIViewer()
    window.resize(480, 320)
    window.show()
    sys.exit(app.exec_())