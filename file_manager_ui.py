"""Native, read-only File Manager screen for PersonalScan."""
from datetime import datetime
import html
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QThread, Signal
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QMessageBox, QProgressBar, QPushButton, QSpinBox, QSplitter, QTabWidget,
    QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from file_manager import ReviewCancelled, review_files


def size_label(value):
    for suffix in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or suffix == "TB":
            return f"{value:.1f} {suffix}"
        value /= 1024


class FileReviewWorker(QThread):
    progress = Signal(int, str)
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, roots, model, age, limit):
        super().__init__()
        self.roots, self.model, self.age, self.limit = list(roots), model, age, limit
        self.ai = None

    def run(self):
        try:
            result = review_files(self.roots, self.model, self.age, self.limit,
                                  self.progress.emit, self.isInterruptionRequested, self.ai_ready)
            if self.isInterruptionRequested():
                raise ReviewCancelled()
            self.result.emit(result)
        except ReviewCancelled:
            self.failed.emit("Cancelled. Files were not changed.")
        except Exception as error:
            if self.isInterruptionRequested():
                self.failed.emit("Cancelled. Files were not changed.")
            else:
                message = str(error) if isinstance(error, (RuntimeError, ValueError)) else "File review could not finish. Check folder access and your local Ollama model."
                self.failed.emit(message)
        finally:
            self.ai = None

    def ai_ready(self, ai):
        self.ai = ai
        if self.isInterruptionRequested() and ai.process and ai.process.poll() is None:
            ai.process.terminate()

    def cancel(self):
        self.requestInterruption()
        ai = self.ai
        # Terminating our owned Ollama server unblocks an in-flight model request.
        if ai and ai.process and ai.process.poll() is None:
            try:
                ai.process.terminate()
            except ProcessLookupError:
                pass


class FileManagerDialog(QDialog):
    def __init__(self, parent, store):
        super().__init__(parent)
        self.store, self.worker = store, None
        self.close_pending = False
        self.setWindowTitle("PersonalScan · File Manager")
        self.resize(1050, 800)
        layout = QVBoxLayout(self)
        heading = QLabel("File Manager"); heading.setStyleSheet("font-size: 24px; font-weight: 600;")
        layout.addWidget(heading)
        intro = QLabel("Local AI suggests older screenshots, schoolwork, and downloads for your review. Files are never deleted or moved.")
        intro.setWordWrap(True); layout.addWidget(intro)
        row = QHBoxLayout()
        self.folders = QListWidget(); self.folders.setMaximumHeight(90)
        defaults = store.config.get("file_review_folders", [])
        for folder in defaults or [str(Path.home() / "Downloads"), str(Path.home() / "Desktop")]:
            self.folders.addItem(folder)
        row.addWidget(self.folders, 1)
        actions = QVBoxLayout()
        self.add_button = QPushButton("+ Add folder…"); self.add_button.clicked.connect(self.add_folder); actions.addWidget(self.add_button)
        self.remove_button = QPushButton("Remove selected"); self.remove_button.clicked.connect(self.remove_folder); actions.addWidget(self.remove_button)
        row.addLayout(actions); layout.addLayout(row)
        options = QHBoxLayout()
        options.addWidget(QLabel("Not modified for at least (days)"))
        self.age = QSpinBox(); self.age.setRange(1, 3650); self.age.setValue(90); options.addWidget(self.age)
        options.addWidget(QLabel("Max files to ask AI about"))
        self.limit = QSpinBox(); self.limit.setRange(1, 200); self.limit.setValue(40); options.addWidget(self.limit)
        options.addStretch()
        self.scan_button = QPushButton("Review with local AI"); self.scan_button.clicked.connect(self.scan); options.addWidget(self.scan_button)
        self.cancel_button = QPushButton("Cancel"); self.cancel_button.clicked.connect(self.cancel); self.cancel_button.setEnabled(False); options.addWidget(self.cancel_button)
        layout.addLayout(options)
        model = QLabel("Local model: " + store.config["model"] + " · Change the model in PersonalScan Settings.")
        model.setTextFormat(Qt.TextFormat.PlainText); layout.addWidget(model)
        self.status = QLabel("Choose folders and start a review.")
        self.status.setWordWrap(True); self.status.setTextFormat(Qt.TextFormat.PlainText); layout.addWidget(self.status)
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0); layout.addWidget(self.progress)
        self.coverage = QLabel("Only filenames, paths, sizes, and dates are inspected. File contents are not read.")
        self.coverage.setWordWrap(True); layout.addWidget(self.coverage)
        self.tabs = QTabWidget(); self.tables = {}
        for key, title in [("review", "Suggested for review"), ("kept", "Keep"), ("errors", "Review failures")]:
            table = QTreeWidget(); table.setHeaderLabels(["File / folder", "Size", "Days since modified", "Location"])
            table.setRootIsDecorated(False); table.setAlternatingRowColors(True)
            table.setColumnWidth(0, 290); table.setColumnWidth(1, 90); table.setColumnWidth(2, 140)
            table.currentItemChanged.connect(self.show_detail)
            self.tables[key] = table; self.tabs.addTab(table, title + " (0)")
        self.detail = QTextBrowser(); self.detail.setOpenLinks(False)
        splitter = QSplitter(Qt.Orientation.Vertical); splitter.addWidget(self.tabs); splitter.addWidget(self.detail); splitter.setSizes([330, 180]); layout.addWidget(splitter, 1)
        self.tabs.currentChanged.connect(self.tab_changed)
        bottom = QHBoxLayout()
        self.reveal_button = QPushButton("Reveal in Finder"); self.reveal_button.clicked.connect(self.reveal)
        self.reveal_button.setEnabled(False); bottom.addWidget(self.reveal_button)
        bottom.addStretch()
        close = QPushButton("Close"); close.clicked.connect(self.close); bottom.addWidget(close)
        layout.addLayout(bottom)
        note = QLabel("Dates do not prove submission or actual use. Filesystem access timestamps can reflect background activity. Review each suggestion before deciding what to keep.")
        note.setWordWrap(True); layout.addWidget(note)

    def add_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Choose a folder to review", str(Path.home()))
        if path and not any(self.folders.item(i).text() == path for i in range(self.folders.count())):
            self.folders.addItem(path)

    def remove_folder(self):
        for item in self.folders.selectedItems():
            self.folders.takeItem(self.folders.row(item))

    def scan(self):
        if self.worker:
            return
        roots = [self.folders.item(i).text() for i in range(self.folders.count())]
        if not roots:
            QMessageBox.information(self, "Choose folders", "Add at least one folder to review."); return
        try:
            self.store.save({**self.store.config, "file_review_folders": roots})
        except (ValueError, OSError):
            QMessageBox.warning(self, "Could not save folders", "Your local settings could not be saved."); return
        self.start_worker(FileReviewWorker(roots, self.store.config["model"], self.age.value(), self.limit.value()))

    def start_worker(self, worker):
        if self.worker:
            return
        self.worker = worker
        for widget in (self.scan_button, self.folders, self.add_button, self.remove_button, self.age, self.limit):
            widget.setEnabled(False)
        self.cancel_button.setEnabled(True); self.progress.setRange(0, 0)
        self.status.setText("Inspecting file metadata…")
        for index, table in enumerate(self.tables.values()):
            table.clear()
            self.tabs.setTabText(index, ["Suggested for review", "Keep", "Review failures"][index] + " (0)")
        self.coverage.setText("Inspecting filenames and metadata in your selected folders…")
        self.detail.clear(); self.reveal_button.setEnabled(False)
        worker.progress.connect(self.update_progress); worker.result.connect(self.complete)
        worker.failed.connect(self.failed); worker.finished.connect(self.worker_finished); worker.start()

    def update_progress(self, value, message):
        self.progress.setRange(0, 100) if value else self.progress.setRange(0, 0)
        self.progress.setValue(value); self.status.setText(message)

    def complete(self, result):
        entries = {"review": result["review"], "kept": result["kept"], "errors": result["errors"] + result["analysis_errors"]}
        for index, (key, table) in enumerate(self.tables.items()):
            table.clear()
            for entry in entries[key]:
                row = QTreeWidgetItem([entry.get("name", Path(entry["path"]).name), size_label(entry["size"]) if "size" in entry else "—", str(entry.get("age_days", "—")), entry["path"]])
                row.setData(0, Qt.ItemDataRole.UserRole, entry); table.addTopLevelItem(row)
            self.tabs.setTabText(index, ["Suggested for review", "Keep", "Review failures"][index] + f" ({len(entries[key])})")
        limits = []
        if result["inventory_limited"]: limits.append("Inventory limit of 5,000 files reached; folder coverage is incomplete.")
        if result["analysis_limited"]: limits.append("AI candidate limit reached; only the oldest candidates were reviewed.")
        if result["errors"]: limits.append("Some folders or files could not be inspected; see Review failures.")
        self.coverage.setText(f"{result['checked']} files checked · {size_label(result.get('review_bytes', 0))} in suggested files. " + " ".join(limits))
        self.progress.setRange(0, 100); self.progress.setValue(100)
        self.tabs.setCurrentIndex(0)
        if self.tables["review"].topLevelItemCount():
            self.tables["review"].setCurrentItem(self.tables["review"].topLevelItem(0))

    def failed(self, message):
        self.progress.setRange(0, 100); self.progress.setValue(0); self.status.setText(message)

    def worker_finished(self):
        worker = self.worker; self.worker = None
        worker.wait(); worker.deleteLater()
        for widget in (self.scan_button, self.folders, self.add_button, self.remove_button, self.age, self.limit):
            widget.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if self.close_pending:
            self.close()

    def cancel(self):
        if self.worker:
            self.worker.cancel()
            self.status.setText("Cancelling local file review…")
            self.cancel_button.setEnabled(False)

    def closeEvent(self, event):
        if self.worker:
            self.close_pending = True; self.cancel(); event.ignore()
        else:
            event.accept()

    def reject(self):
        # Escape must follow the same worker cleanup path as the Close button.
        if self.worker:
            self.close_pending = True; self.cancel()
        else:
            super().reject()

    def tab_changed(self):
        table = self.tabs.currentWidget()
        if table.currentItem(): self.show_detail(table.currentItem())
        else: self.detail.clear(); self.reveal_button.setEnabled(False)

    def show_detail(self, row, previous=None):
        if not row or row.treeWidget() is not self.tabs.currentWidget():
            return
        entry = row.data(0, Qt.ItemDataRole.UserRole)
        escape = lambda value: html.escape(str(value))
        content = f"<h3>{escape(entry.get('name', Path(entry['path']).name))}</h3><p>{escape(entry['path'])}</p>"
        content += f"<p><b>Local AI suggestion:</b> {escape(entry['reason'])}</p>" if "decision" in entry else f"<p>{escape(entry['reason'])}</p>"
        if "modified" in entry:
            content += f"<p>Modified: {escape(datetime.fromtimestamp(entry['modified']).strftime('%b %d, %Y %I:%M %p'))}<br>Filesystem access timestamp: {escape(datetime.fromtimestamp(entry['accessed']).strftime('%b %d, %Y %I:%M %p'))}</p>"
        for key, fact in entry.get("facts", {}).items():
            content += f"<p>{'<b>AI evidence · </b>' if key in entry.get('evidence', []) else ''}{escape(fact)}</p>"
        self.detail.setHtml(content); self.reveal_button.setEnabled(True)

    def reveal(self):
        row = self.tabs.currentWidget().currentItem()
        if not row:
            return
        path = row.data(0, Qt.ItemDataRole.UserRole)["path"]
        if not Path(path).exists():
            QMessageBox.information(self, "File unavailable", "The file or folder no longer exists at this location."); return
        QProcess.startDetached("/usr/bin/open", ["-R", path])
