"""Rate-limited bug report and feature suggestion dialog."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from ..telemetry import ReportingWorker, make_feedback


class FeedbackDialog(QDialog):
    """Collect one short report and send it to the encrypted dashboard collector."""

    completed = pyqtSignal(bool, str)

    def __init__(self, store, category: str | None = None, parent=None):
        super().__init__(parent)
        self.store = store
        self.category = category
        self.sent = False
        self._worker = None
        self.setWindowTitle("Send Offline Chess feedback")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        layout = QVBoxLayout(self)
        prompt = QLabel("Your message, app version, install ID, and saved Chess.com username go to the maintainer dashboard. Game PGNs are not attached. Limit: 5 messages per 24 hours, at least 60 seconds apart. Do not include sensitive information.")
        prompt.setObjectName("Muted")
        prompt.setWordWrap(True)
        layout.addWidget(prompt)

        if category is None:
            self.category_choice = QComboBox()
            self.category_choice.addItem("Bug report", "bug")
            self.category_choice.addItem("Feature suggestion", "feature")
            layout.addWidget(self.category_choice)
        else:
            self.category_choice = None

        self.message = QTextEdit()
        self.message.setPlaceholderText("What happened, or what would you like to see?")
        self.message.setMaximumHeight(200)
        self.message.textChanged.connect(self._update_count)
        layout.addWidget(self.message)
        self.character_count = QLabel("0 / 3,000 characters")
        self.character_count.setObjectName("Muted")
        layout.addWidget(self.character_count)

        self.status = QLabel("")
        self.status.setObjectName("ReviewSummary")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("PrimaryButton")
        self.send_button.setEnabled(False)
        self.send_button.clicked.connect(self._send)
        buttons.addWidget(self.send_button)
        layout.addLayout(buttons)

    def _update_count(self):
        count = len(self.message.toPlainText().strip())
        self.character_count.setText(f"{count:,} / 3,000 characters")
        self.send_button.setEnabled(1 <= count <= 3000 and not self._is_sending())

    def _is_sending(self):
        return bool(self._worker and self._worker.isRunning())

    def _send(self):
        category = self.category or self.category_choice.currentData()
        try:
            payload = make_feedback(self.store, category, self.message.toPlainText())
        except (ValueError, OSError, RuntimeError) as exc:
            self.status.setText(str(exc))
            return

        self.send_button.setEnabled(False)
        self.send_button.setText("Sending…")
        self.cancel_button.setEnabled(False)
        if self.category_choice:
            self.category_choice.setEnabled(False)
        self.message.setEnabled(False)
        self.status.setText("Sending securely to the app maintainer’s feedback dashboard…")
        self._worker = ReportingWorker("feedback", payload, self)
        self._worker.finished_with_result.connect(self._finish_send)
        self._worker.start()

    def _finish_send(self, ok, error):
        self.completed.emit(bool(ok), str(error or ""))
        if ok:
            self.sent = True
            self.accept()
            return
        self.status.setText("The message could not be sent. Check your connection and try again.")
        self.message.setEnabled(True)
        self.cancel_button.setEnabled(True)
        if self.category_choice:
            self.category_choice.setEnabled(True)
        self.send_button.setText("Send")
        self._update_count()

    def reject(self):
        if self._is_sending():
            return
        super().reject()
