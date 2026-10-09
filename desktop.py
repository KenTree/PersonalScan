"""PersonalScan's native Mac desktop application. No HTTP server or web UI."""
import argparse
import contextlib
from datetime import datetime
import html
import io
import json
import os
from pathlib import Path
import re
import sys
import subprocess
import time
from urllib.parse import quote
import uuid

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QSpinBox, QSplitter,
    QTabWidget, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from settings import load_config
from scanner import demo_threads, digest_thread
from providers import gmail_service, read_threads, LocalAI
from file_manager_ui import FileManagerDialog

RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "config.json"
        self.credentials = self.root / "credentials.json"
        self.config = load_config(self.path)

    def save(self, config):
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(config, indent=2) + "\n")
        temporary.chmod(0o600)
        try:
            validated = load_config(temporary)
            temporary.replace(self.path)
            self.config = validated
        finally:
            temporary.unlink(missing_ok=True)

    def import_credentials(self, source):
        content = Path(source).read_text()
        value = json.loads(content).get("installed", {})
        if not value.get("client_id") or not value.get("client_secret"):
            raise ValueError("Choose the downloaded Google Desktop app OAuth client JSON.")
        self.credentials.write_text(content)
        self.credentials.chmod(0o600)

    def migrate(self, source):
        source = Path(source)
        if not self.path.exists() and (source / "config.json").is_file():
            self.save(load_config(source / "config.json"))
        if not self.credentials.exists() and (source / "credentials.json").is_file():
            self.import_credentials(source / "credentials.json")


class Cancelled(Exception):
    pass


class Worker(QThread):
    progress = Signal(int, str)
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, store, account, limit, ai=False, demo=False, authorize=False):
        super().__init__()
        self.config = json.loads(json.dumps(store.config))
        self.credentials = store.credentials
        self.accounts = [dict(a) for a in account] if isinstance(account, list) else [dict(account)]
        if not self.accounts:
            raise ValueError("Choose at least one Gmail account.")
        self.account = self.accounts[0]
        self.account_index = 0
        self.limit, self.ai, self.demo, self.authorize = limit, ai, demo, authorize

    def report(self, percent, message):
        if self.isInterruptionRequested():
            raise Cancelled()
        self.progress.emit(percent, message)

    def account_progress(self, percent, message):
        value = int((self.account_index * 100 + percent) / len(self.accounts))
        self.report(value, f"Account {self.account_index + 1} of {len(self.accounts)} · {self.account['label']}: {message}")

    def retrieval_progress(self, message):
        match = re.search(r"Reading thread (\d+) of (\d+)", message)
        percent = 10 + int(50 * int(match[1]) / max(1, int(match[2]))) if match else 5
        self.account_progress(percent, message)

    @staticmethod
    def error_message(error):
        return str(error) if isinstance(error, (RuntimeError, ValueError)) else "Could not complete the operation. Check Gmail authorization and your network connection."

    def scan_account(self, ai):
        failures = []
        self.account_progress(0, "Connecting to Gmail…")
        if self.demo:
            samples = demo_threads()
            threads, capped = samples[:self.limit], len(samples) > self.limit
            effective = {**self.config, "mentor_emails": ["contact@example.com"], "riot_domains": ["studio.example"], "bank_domains": ["bank.example", "otherbank.example"]}
            email = self.account["email"] or "demo@example.com"
        else:
            service = gmail_service(self.credentials, account_id=self.account["id"], expected_email=self.account["email"])
            email = service.users().getProfile(userId="me").execute(num_retries=2)["emailAddress"]
            threads, _, capped = read_threads(service, self.config["lookback_days"], self.limit, self.retrieval_progress, failures)
            effective = self.config
        origin = {"account_id": self.account["id"], "account_label": self.account["label"], "account_email": email}
        failures = [{**failure, **origin} for failure in failures]
        notable, excluded, scanned = [], [], []
        ai_failures = 0
        for index, messages in enumerate(threads):
            percent = 65 + int(34 * index / max(1, len(threads)))
            self.account_progress(percent, f"Evaluating thread {index + 1} of {len(threads)}…")
            item, reason = digest_thread(messages, effective)
            latest = sorted(messages, key=lambda m: m.received)[-1] if messages else None
            entry = {**origin, "subject": latest.subject if latest else "Empty thread", "sender": latest.sender if latest else "", "reason": reason or "Notable thread", "item": item,
                     "messages": [{"sender": m.sender, "body": m.body, "received": m.received} for m in messages]}
            if item:
                item["url"] = None if self.demo else "https://mail.google.com/mail/?authuser=" + quote(email, safe="") + "#all/" + quote(item["thread_id"], safe="")
                if ai:
                    self.account_progress(percent, f"Summarizing thread {index + 1} locally…")
                    try:
                        ai.summarize(item)
                    except Exception:
                        ai_failures += 1
                        item["warnings"].append("Local AI could not produce a verified summary; source excerpts retained.")
                item.pop("context", None)
                notable.append(entry)
            else:
                excluded.append(entry)
            scanned.append(entry)
        notices = []
        if capped:
            notices.append("Thread limit reached; older matching threads may remain.")
        if failures:
            notices.append(f"{len(failures)} retrieval failure(s); coverage is incomplete.")
        if ai_failures:
            notices.append(f"{ai_failures} AI summaries used source excerpts instead.")
        self.account_progress(100, "Finished. " + " ".join(notices))
        summary = {**origin, "scanned": len(scanned), "notable": len(notable), "excluded": len(excluded), "failed": len(failures), "notices": notices, "success": True}
        return {"scanned": scanned, "notable": notable, "excluded": excluded, "failed": failures}, summary

    def run(self):
        ai = None
        try:
            self.report(0, "Opening Google sign-in…" if self.authorize else "Starting scan…")
            if self.authorize:
                self.authorize_gmail()
                self.report(100, "Gmail authorized with read-only access.")
                self.result.emit({"authorized": self.account["id"]})
                return
            if self.ai:
                self.report(0, "Starting local AI model…")
                os.environ["PATH"] = os.pathsep.join([os.environ.get("PATH", ""), "/opt/homebrew/bin", "/usr/local/bin", "/Applications/Ollama.app/Contents/Resources"])
                ai = LocalAI(self.config["model"])
            result = {key: [] for key in ("scanned", "notable", "excluded", "failed")}
            summaries = []
            for index, account in enumerate(self.accounts):
                self.account_index, self.account = index, account
                try:
                    entries, summary = self.scan_account(ai)
                    for key in result:
                        result[key].extend(entries[key])
                    summaries.append(summary)
                except Cancelled:
                    raise
                except Exception as error:
                    message = self.error_message(error)
                    origin = {"account_id": account["id"], "account_label": account["label"], "account_email": account["email"]}
                    result["failed"].append({**origin, "thread_id": "Account connection", "reason": message})
                    summaries.append({**origin, "scanned": 0, "notable": 0, "excluded": 0, "failed": 1, "success": False, "notices": [message]})
                    self.account_progress(100, "Account could not be scanned; continuing to the next account.")
            result["notable"].sort(key=lambda e: e["item"]["received"], reverse=True)
            notices = [f"{s['account_label']}: {notice}" for s in summaries for notice in s["notices"]]
            if not self.config["mentor_emails"] and not self.demo:
                notices.append("Add mentor addresses in Settings to enable mentor detection.")
            successful = sum(s["success"] for s in summaries)
            status = f"Scan finished. {successful} of {len(summaries)} account(s) scanned · {len(result['notable'])} notable thread(s). " + " ".join(notices)
            self.report(100, status)
            result.update(account_summaries=summaries, status=status, finished=datetime.now().strftime("%b %d, %Y at %I:%M %p"))
            self.result.emit(result)
        except Cancelled:
            self.failed.emit("Cancelled. Any previous results are from the last completed scan.")
        except Exception as error:
            self.failed.emit(self.error_message(error))
        finally:
            if ai:
                ai.close()

    def authorize_gmail(self):
        # Browser authorization can block waiting for a callback that Google never
        # sends after denying access. Isolate it so cancellation also stops token
        # requests and closes the callback listener, without terminating QThreads.
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command.append(str(Path(__file__).resolve()))
        command.extend(["--authorize-helper", "--credentials", str(self.credentials),
                        "--account-id", self.account["id"], "--expected-email", self.account["email"]])
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        deadline = time.monotonic() + 185
        try:
            while True:
                if self.isInterruptionRequested():
                    raise Cancelled()
                if time.monotonic() >= deadline:
                    raise RuntimeError("Gmail authorization timed out. Try again or check Google's access settings.")
                try:
                    output, _ = process.communicate(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    pass
            if self.isInterruptionRequested():
                raise Cancelled()
            try:
                result = json.loads(output)
            except (ValueError, TypeError):
                raise RuntimeError("Gmail authorization could not finish. Try authorizing again.") from None
            if process.returncode != 0 or not result.get("authorized"):
                raise RuntimeError(result.get("error", "Gmail authorization failed. Try again or check Google's access settings."))
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if process.stdout:
                process.stdout.close()


def authorization_helper(credentials, account_id, expected_email):
    """Only this short-lived helper performs the blocking browser OAuth flow."""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            gmail_service(credentials, authorize=True, account_id=account_id, expected_email=expected_email)
        print(json.dumps({"authorized": account_id}), flush=True)
        return 0
    except Exception as error:
        message = str(error) if isinstance(error, RuntimeError) else "Google authorization was denied or could not complete. Check the project's test users and Gmail API access, then try again."
        print(json.dumps({"error": message}), flush=True)
        return 1


def button(text, callback):
    widget = QPushButton(text)
    widget.clicked.connect(callback)
    return widget


class RuleListDialog(QDialog):
    """Edit a private draft; accepting is the only way changes leave this dialog."""
    def __init__(self, parent, title, values, emails=False):
        super().__init__(parent)
        self.emails = emails
        self.values = list(values)
        self.setWindowTitle(title)
        self.resize(520, 400)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Email addresses" if emails else "Sender domains (for example, bank.com)"))
        self.entries = QListWidget()
        for value in values:
            self.add_row(value)
        layout.addWidget(self.entries)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("name@example.com" if emails else "example.com")
        self.input.returnPressed.connect(self.add_entry)
        row.addWidget(self.input)
        add = button("+", self.add_entry); add.setAccessibleName("Add entry")
        row.addWidget(add); layout.addLayout(row)
        layout.addWidget(button("Remove selected", self.remove_selected))
        self.error = QLabel(); self.error.setWordWrap(True)
        self.error.setTextFormat(Qt.TextFormat.PlainText); layout.addWidget(self.error)
        layout.addWidget(QLabel("Changes are applied when you save Settings. Double-click an entry to edit it."))
        controls = QDialogButtonBox()
        controls.addButton("Save and close", QDialogButtonBox.ButtonRole.AcceptRole)
        controls.addButton("Leave without saving", QDialogButtonBox.ButtonRole.RejectRole)
        controls.accepted.connect(self.save_and_close); controls.rejected.connect(self.reject)
        layout.addWidget(controls)

    def normalize(self, value):
        value = value.strip().lower()
        if self.emails:
            valid = re.fullmatch(r"[^\s@,]+@[^\s@,]+\.[^\s@,]+", value)
        else:
            valid = len(value) <= 253 and "." in value and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in value.split("."))
        if not valid:
            raise ValueError("Enter a valid email address." if self.emails else "Enter a domain such as bank.com, without https:// or an email address.")
        return value

    def add_row(self, value):
        item = QListWidgetItem(value)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.entries.addItem(item)
        self.entries.setCurrentItem(item)

    def add_entry(self):
        try:
            value = self.normalize(self.input.text())
            if any(self.entries.item(i).text().strip().lower() == value for i in range(self.entries.count())):
                raise ValueError("This entry is already in the list.")
        except ValueError as error:
            self.error.setText(str(error)); return False
        self.add_row(value); self.input.clear(); self.error.clear()
        return True

    def remove_selected(self):
        for item in self.entries.selectedItems():
            self.entries.takeItem(self.entries.row(item))

    def save_and_close(self):
        # Do not silently discard a value typed without pressing +.
        if self.input.text().strip() and not self.add_entry():
            return
        try:
            values = [self.normalize(self.entries.item(i).text()) for i in range(self.entries.count())]
            if len(set(values)) != len(values):
                raise ValueError("Remove duplicate entries before saving.")
        except ValueError as error:
            self.error.setText(str(error)); return
        self.values = values
        self.accept()


class SettingsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("PersonalScan Settings")
        self.resize(650, 530)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Gmail accounts"))
        self.accounts = QTreeWidget()
        self.accounts.setHeaderLabels(["Name", "Gmail address"])
        for account in window.store.config["gmail_accounts"]:
            self.add_row(account)
        layout.addWidget(self.accounts)
        fields = QHBoxLayout()
        self.label = QLineEdit(); self.label.setPlaceholderText("Account name")
        self.email = QLineEdit(); self.email.setPlaceholderText("you@gmail.com")
        fields.addWidget(self.label); fields.addWidget(self.email)
        fields.addWidget(button("Add account", self.add_account))
        layout.addLayout(fields)
        row = QHBoxLayout()
        row.addWidget(button("Remove selected", self.remove_account))
        row.addWidget(button("Authorize selected…", self.authorize))
        row.addWidget(button("Import OAuth JSON…", self.import_credentials))
        layout.addLayout(row)
        self.credential_status = QLabel("OAuth credentials imported" if window.store.credentials.exists() else "Import your Google Desktop OAuth client JSON to connect Gmail.")
        self.credential_status.setWordWrap(True)
        layout.addWidget(self.credential_status)
        form = QFormLayout()
        self.fields = {}
        self.rule_values = {}
        for key, title in [("mentor_emails", "Mentor email addresses"), ("riot_domains", "Game studio domains"), ("bank_domains", "Bank domains")]:
            self.rule_values[key] = list(window.store.config[key])
            widget = button(f"Edit list… ({len(self.rule_values[key])})", lambda checked=False, key=key, title=title: self.edit_rules(key, title))
            self.fields[key] = widget
            form.addRow(title, widget)
        self.days = QSpinBox(); self.days.setRange(1, 365); self.days.setValue(window.store.config["lookback_days"])
        form.addRow("Look back (days)", self.days)
        self.model = QLineEdit(window.store.config["model"])
        form.addRow("Local Ollama model", self.model)
        layout.addLayout(form)
        note = QLabel("Gmail access is read-only. Authorization stays in macOS Keychain. Removing an account from this list does not revoke Google's grant.")
        note.setWordWrap(True); layout.addWidget(note)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        controls.button(QDialogButtonBox.StandardButton.Save).setText("Save and close")
        controls.button(QDialogButtonBox.StandardButton.Cancel).setText("Leave without saving")
        controls.accepted.connect(self.save_and_close); controls.rejected.connect(self.reject)
        layout.addWidget(controls)

    def edit_rules(self, key, title):
        dialog = RuleListDialog(self, title, self.rule_values[key], emails=key == "mentor_emails")
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.rule_values[key] = dialog.values
            self.fields[key].setText(f"Edit list… ({len(dialog.values)})")

    def add_row(self, account):
        item = QTreeWidgetItem([account["label"], account["email"]])
        item.setData(0, Qt.ItemDataRole.UserRole, dict(account))
        self.accounts.addTopLevelItem(item)
        self.accounts.setCurrentItem(item)

    def add_account(self):
        email, label = self.email.text().strip(), self.label.text().strip()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or not label or len(label) > 100:
            QMessageBox.warning(self, "Account details", "Enter an account name and a valid email address.")
            return
        for i in range(self.accounts.topLevelItemCount()):
            if self.accounts.topLevelItem(i).text(1).casefold() == email.casefold():
                QMessageBox.warning(self, "Account exists", "This email is already in your accounts.")
                return
        self.add_row({"id": "gmail-" + uuid.uuid4().hex[:12], "label": label, "email": email})
        self.label.clear(); self.email.clear()

    def remove_account(self):
        item = self.accounts.currentItem()
        if item:
            self.accounts.takeTopLevelItem(self.accounts.indexOfTopLevelItem(item))

    def persist(self):
        config = {**self.window.store.config}
        config["gmail_accounts"] = [self.accounts.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) for i in range(self.accounts.topLevelItemCount())]
        for key, values in self.rule_values.items():
            config[key] = list(values)
        config["lookback_days"] = self.days.value()
        config["model"] = self.model.text().strip()
        try:
            self.window.store.save(config)
            self.window.refresh_accounts()
            return True
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Could not save settings", str(error))
            return False

    def save_and_close(self):
        if self.persist():
            self.accept()

    def authorize(self):
        item = self.accounts.currentItem()
        if not item:
            return
        if not self.window.store.credentials.exists():
            QMessageBox.information(self, "OAuth credentials needed", "Import the Google Desktop OAuth client JSON first.")
            return
        account = item.data(0, Qt.ItemDataRole.UserRole)
        if self.persist():
            self.accept()
            self.window.start_authorization(account)

    def import_credentials(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Google Desktop OAuth client", str(Path.home() / "Downloads"), "JSON files (*.json)")
        if path:
            try:
                self.window.store.import_credentials(path)
                self.credential_status.setText("OAuth credentials imported")
            except (ValueError, OSError):
                QMessageBox.warning(self, "Invalid credentials", "Choose a valid Google Desktop OAuth client JSON.")


class MainWindow(QMainWindow):
    def __init__(self, store, demo=False):
        super().__init__()
        self.store, self.demo = store, demo
        self.worker = None
        self.close_pending = False
        self.setWindowTitle("PersonalScan" + (" — Demo" if demo else ""))
        self.resize(1120, 780)
        self.setMinimumSize(800, 560)
        self.setWindowIcon(QIcon(str(RESOURCE_ROOT / "PersonalScanIcon.jpeg")))
        central = QWidget(); self.setCentralWidget(central)
        layout = QVBoxLayout(central); layout.setContentsMargins(24, 20, 24, 20); layout.setSpacing(12)
        heading = QHBoxLayout()
        title = QLabel("PersonalScan"); title.setStyleSheet("font-size: 26px; font-weight: 600;")
        heading.addWidget(title); heading.addStretch()
        self.settings_button = button("Settings…", self.settings)
        self.files_button = button("File Manager…", self.file_manager)
        heading.addWidget(self.files_button)
        heading.addWidget(self.settings_button); layout.addLayout(heading)
        layout.addWidget(QLabel("Your Gmail, organized into the threads worth your attention."))
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Gmail account"))
        self.account = QComboBox(); self.account.setMinimumWidth(280)
        controls.addWidget(self.account, 1)
        controls.addWidget(QLabel("Threads per account"))
        self.limit = QSpinBox(); self.limit.setRange(1, 150); self.limit.setValue(store.config["max_threads"])
        self.limit.setToolTip("Read up to this many threads from each account in the scan.")
        controls.addWidget(self.limit)
        self.scan_button = button("Scan demo" if demo else "Scan Gmail", self.scan)
        self.scan_button.setDefault(True); controls.addWidget(self.scan_button)
        self.cancel_button = button("Cancel", self.cancel); self.cancel_button.setEnabled(False)
        controls.addWidget(self.cancel_button); layout.addLayout(controls)
        row = QHBoxLayout()
        self.ai = QCheckBox("Use local AI summaries (Ollama)"); row.addWidget(self.ai)
        self.authorize_button = button("Authorize Gmail…", self.authorize_current); row.addWidget(self.authorize_button)
        row.addStretch(); layout.addLayout(row)
        self.status = QLabel("Ready. Choose an account and start a scan."); self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.last_scan = QLabel("No completed scan yet."); layout.addWidget(self.last_scan)
        self.tabs = QTabWidget(); self.tables = {}
        for key, title in [("scanned", "Threads scanned"), ("notable", "Notable threads"), ("excluded", "Excluded threads"), ("failed", "Retrieval failures")]:
            table = QTreeWidget(); table.setHeaderLabels(["Thread" if key == "failed" else "Subject", "Gmail account", "Reason" if key == "failed" else "Sender", "Category / reason"])
            table.setRootIsDecorated(False); table.setAlternatingRowColors(True)
            table.setColumnWidth(0, 320); table.setColumnWidth(1, 230)
            table.currentItemChanged.connect(self.show_detail)
            self.tables[key] = table; self.tabs.addTab(table, title + " (0)")
        self.detail = QTextBrowser(); self.detail.setOpenLinks(False); self.detail.anchorClicked.connect(self.open_link)
        self.summary = QTextBrowser(); self.summary.setOpenLinks(False); self.summary.anchorClicked.connect(self.open_link)
        self.summary.setPlaceholderText("Scan multiple accounts to see a combined digest grouped by Gmail address.")
        self.tabs.addTab(self.summary, "Combined summary")
        self.detail.setPlaceholderText("Select a thread to read its summary and original message text.")
        splitter = QSplitter(Qt.Orientation.Vertical); splitter.addWidget(self.tabs); splitter.addWidget(self.detail)
        splitter.setSizes([330, 220]); layout.addWidget(splitter, 1)
        footer = QLabel("Gmail read-only · Processing on this Mac · Email images and HTML are never loaded")
        layout.addWidget(footer)
        self.refresh_accounts()
        self.account.currentIndexChanged.connect(self.clear_results)
        self.tabs.currentChanged.connect(self.tab_changed)
        menu = self.menuBar().addMenu("PersonalScan")
        action = QAction("Settings…", self); action.setShortcut("Ctrl+,"); action.triggered.connect(self.settings); menu.addAction(action)
        self.settings_action = action

    def refresh_accounts(self):
        old = self.account.currentData()
        self.account.blockSignals(True); self.account.clear()
        self.account.addItem("All Gmail accounts", None)
        for account in self.store.config["gmail_accounts"]:
            self.account.addItem(account["label"] + (" · " + account["email"] if account["email"] else ""), account["id"])
        index = self.account.findData(old)
        self.account.setCurrentIndex(max(0, index)); self.account.blockSignals(False)
        self.clear_results()

    def current_account(self):
        if self.account.currentData() is None:
            return self.store.config["gmail_accounts"][0]
        return next(a for a in self.store.config["gmail_accounts"] if a["id"] == self.account.currentData())

    def clear_results(self):
        for index, (key, table) in enumerate(self.tables.items()):
            table.clear(); self.tabs.setTabText(index, ["Threads scanned", "Notable threads", "Excluded threads", "Retrieval failures"][index] + " (0)")
        self.detail.clear(); self.last_scan.setText("No completed scan for this account yet.")
        self.summary.clear()
        self.progress.setValue(0)

    def settings(self):
        if not self.worker:
            SettingsDialog(self).exec()

    def file_manager(self):
        if not self.worker:
            FileManagerDialog(self, self.store).exec()

    def authorize_current(self):
        if self.account.currentData() is None and len(self.store.config["gmail_accounts"]) > 1:
            QMessageBox.information(self, "Choose an account", "Select one Gmail account to authorize it, or use Authorize selected in Settings.")
            return
        self.start_authorization(self.current_account())

    def start_authorization(self, account):
        if not self.store.credentials.exists():
            QMessageBox.information(self, "OAuth credentials needed", "Open Settings and import your Google Desktop OAuth client JSON first.")
            return
        self.start_worker(Worker(self.store, account, self.limit.value(), authorize=True))

    def scan(self):
        try:
            self.store.save({**self.store.config, "max_threads": self.limit.value()})
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Could not save scan limit", str(error)); return
        accounts = self.store.config["gmail_accounts"] if self.account.currentData() is None else [self.current_account()]
        self.start_worker(Worker(self.store, accounts, self.limit.value(), ai=self.ai.isChecked(), demo=self.demo))

    def start_worker(self, worker):
        if self.worker:
            return
        self.worker = worker
        for widget in [self.scan_button, self.account, self.limit, self.ai, self.settings_button, self.authorize_button, self.files_button]:
            widget.setEnabled(False)
        self.settings_action.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self.status.setText("Starting…")
        worker.progress.connect(self.update_progress)
        worker.result.connect(self.complete)
        worker.failed.connect(self.failed)
        worker.finished.connect(self.worker_finished)
        worker.start()

    def update_progress(self, value, message):
        self.progress.setRange(0, 100) if value > 0 else self.progress.setRange(0, 0)
        self.progress.setValue(value); self.status.setText(message)

    def complete(self, result):
        if "authorized" in result:
            self.status.setText("Gmail authorized. You can now scan this account.")
            return
        self.clear_results()
        for index, key in enumerate(self.tables):
            table = self.tables[key]
            for entry in result[key]:
                item = entry.get("item")
                row = QTreeWidgetItem([entry.get("subject", entry.get("thread_id", "")), entry.get("account_email") or entry.get("account_label", ""), entry.get("sender", entry.get("reason", "")), item["category"] if item else entry.get("reason", "")])
                row.setData(0, Qt.ItemDataRole.UserRole, entry); table.addTopLevelItem(row)
            self.tabs.setTabText(index, ["Threads scanned", "Notable threads", "Excluded threads", "Retrieval failures"][index] + f" ({len(result[key])})")
        self.progress.setValue(100)
        self.status.setText(result.get("status", "Scan finished."))
        self.last_scan.setText("Last completed scan: " + result["finished"])
        self.render_summary(result)
        self.tabs.setCurrentIndex(4 if len(result.get("account_summaries", [])) > 1 else 1)
        if self.tables["notable"].topLevelItemCount():
            self.tables["notable"].setCurrentItem(self.tables["notable"].topLevelItem(0))

    def render_summary(self, result):
        escape = lambda value: html.escape(str(value))
        content = f"<h2>Combined digest</h2><p>{len(result['notable'])} notable threads across {len(result.get('account_summaries', []))} Gmail account(s).</p>"
        for account in result.get("account_summaries", []):
            content += f"<h3>{escape(account['account_label'])} · {escape(account['account_email'])}</h3>"
            if account["success"]:
                content += f"<p>{account['scanned']} scanned · {account['notable']} notable · {account['excluded']} excluded · {account['failed']} retrieval failures</p>"
            else:
                content += "<p><b>This account could not be scanned.</b></p>"
            for notice in account["notices"]:
                content += f"<p>{escape(notice)}</p>"
            entries = [e for e in result["notable"] if e["account_id"] == account["account_id"]]
            for entry in entries:
                item = entry["item"]
                content += f"<p><b>{escape(item['subject'])}</b> · {escape(item['category'])}<br>{escape(item['summary'])}</p>"
                if item.get("url"):
                    content += f'<p><a href="{escape(item["url"])}">Open in {escape(account["account_email"])}</a></p>'
            if account["success"] and not entries:
                content += "<p>No notable threads found in this account's scan.</p>"
        self.summary.setHtml(content)

    def failed(self, message):
        self.progress.setRange(0, 100); self.progress.setValue(0)
        self.status.setText(message)

    def worker_finished(self):
        worker = self.worker
        self.worker = None
        worker.wait(); worker.deleteLater()
        for widget in [self.scan_button, self.account, self.limit, self.ai, self.settings_button, self.authorize_button, self.files_button]:
            widget.setEnabled(True)
        self.settings_action.setEnabled(True); self.cancel_button.setEnabled(False)
        if self.close_pending:
            self.close()

    def cancel(self):
        if self.worker:
            self.worker.requestInterruption()
            self.status.setText("Cancelling Gmail authorization…" if self.worker.authorize else "Cancelling after the current Gmail or model request completes…")
            self.cancel_button.setEnabled(False)

    def closeEvent(self, event):
        if self.worker:
            self.close_pending = True; self.cancel(); event.ignore()
        else:
            event.accept()

    def tab_changed(self):
        table = self.tabs.currentWidget()
        self.detail.setVisible(table is not self.summary)
        if table is self.summary:
            self.detail.clear()
            return
        if table.currentItem():
            self.show_detail(table.currentItem())
        else:
            self.detail.clear()

    def show_detail(self, row, previous=None):
        if not row:
            return
        if row.treeWidget() is not self.tabs.currentWidget():
            return
        entry = row.data(0, Qt.ItemDataRole.UserRole)
        escape = lambda value: html.escape(str(value))
        content = f"<h2>{escape(entry.get('subject', 'Retrieval failure'))}</h2><p>{escape(entry.get('sender', ''))}</p>"
        content += f"<p><b>Gmail account:</b> {escape(entry.get('account_email') or entry.get('account_label', ''))}</p>"
        item = entry.get("item")
        if item:
            content += f"<p><b>{escape(item['category'])}</b> · {escape(item['summary_type'])}</p><p>{escape(item['summary'])}</p><p>{escape(item['reason'])}</p>"
            if item.get("action_quote"):
                content += f"<p><b>Requested action:</b> {escape(item['action_quote'])}</p>"
            for evidence in item.get("evidence", []):
                content += f"<blockquote>{escape(evidence)}</blockquote>"
            for warning in item.get("warnings", []):
                content += f"<p>{escape(warning)}</p>"
            if item.get("url"):
                content += f'<p><a href="{escape(item["url"])}">Open original in Gmail</a></p>'
        else:
            content += f"<p>{escape(entry.get('reason', ''))}</p>"
        messages = item["messages"] if item else entry.get("messages", [])
        for mail in messages:
            content += f"<hr><p><b>{escape(mail['sender'])}</b> · {escape(mail['received'])}</p><p>{escape(mail['body']).replace(chr(10), '<br>')}</p>"
        self.detail.setHtml(content)

    def open_link(self, url):
        if url.scheme() == "https" and url.host() == "mail.google.com":
            QDesktopServices.openUrl(url)


def main():
    parser = argparse.ArgumentParser(description="PersonalScan native desktop")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--import-from", type=Path)
    parser.add_argument("--authorize-helper", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--credentials", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--account-id", help=argparse.SUPPRESS)
    parser.add_argument("--expected-email", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.authorize_helper:
        if not args.credentials or not args.account_id:
            parser.error("Authorization helper requires credentials and an account ID.")
        return authorization_helper(args.credentials, args.account_id, args.expected_email)
    app = QApplication(sys.argv[:1]); app.setApplicationName("PersonalScan"); app.setOrganizationName("PersonalScan")
    root = args.data_dir or Path.home() / "Library" / "Application Support" / "PersonalScan"
    try:
        store = Store(root)
        if args.import_from:
            store.migrate(args.import_from)
        elif not getattr(sys, "frozen", False) and not args.data_dir:
            store.migrate(Path(__file__).resolve().parent)
        window = MainWindow(store, demo=args.demo)
    except (OSError, ValueError) as error:
        QMessageBox.critical(None, "PersonalScan setup", str(error)); return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
