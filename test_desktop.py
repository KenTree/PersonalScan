import json
import os
import contextlib
import io
import subprocess
import threading
import time
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from desktop import MainWindow, RuleListDialog, SettingsDialog, Store, Worker, authorization_helper
from scanner import demo_threads

APP = QApplication.instance() or QApplication([])


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "data")

    def tearDown(self):
        self.temp.cleanup()

    def test_migration_preserves_ids_and_does_not_overwrite_desktop_settings(self):
        source = Path(self.temp.name) / "source"; source.mkdir()
        config = {"gmail_accounts": [{"id": "main", "label": "Main", "email": "me@example.com"}]}
        (source / "config.json").write_text(json.dumps(config))
        (source / "credentials.json").write_text(json.dumps({"installed": {"client_id": "mock", "client_secret": "mock"}}))
        self.store.migrate(source)
        self.assertEqual(self.store.config["gmail_accounts"][0]["id"], "main")
        self.store.save({**self.store.config, "max_threads": 12})
        self.store.migrate(source)
        self.assertEqual(self.store.config["max_threads"], 12)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.credentials.stat().st_mode & 0o777, 0o600)

    def test_invalid_settings_leave_saved_config_intact(self):
        self.store.save(self.store.config)
        previous = self.store.path.read_text()
        with self.assertRaises(ValueError):
            self.store.save({**self.store.config, "gmail_accounts": []})
        self.assertEqual(self.store.path.read_text(), previous)

    def test_demo_scan_populates_all_result_sections(self):
        worker = Worker(self.store, self.store.config["gmail_accounts"][0], 40, demo=True)
        results, errors, progress = [], [], []
        worker.result.connect(results.append); worker.failed.connect(errors.append)
        worker.progress.connect(lambda value, message: progress.append(value))
        worker.run()
        self.assertEqual(errors, [])
        result = results[0]
        self.assertEqual(len(result["scanned"]), len(demo_threads()))
        self.assertEqual(len(result["notable"]) + len(result["excluded"]), len(result["scanned"]))
        self.assertEqual(result["failed"], [])
        self.assertEqual(progress[-1], 100)
        window = MainWindow(self.store, demo=True)
        window.complete(result)
        self.assertGreater(window.tables["notable"].topLevelItemCount(), 0)
        self.assertIn("Last completed scan", window.last_scan.text())
        self.assertIn("Source excerpts", window.detail.toPlainText())
        window.clear_results()
        self.assertEqual(window.tables["notable"].topLevelItemCount(), 0)
        window.close()

    def test_settings_add_account_persists_and_updates_selector(self):
        window = MainWindow(self.store)
        dialog = SettingsDialog(window)
        dialog.label.setText("Second Gmail"); dialog.email.setText("second@example.com")
        dialog.add_account()
        self.assertTrue(dialog.persist())
        self.assertEqual(window.account.count(), 3)
        account = self.store.config["gmail_accounts"][1]
        self.assertEqual(account["email"], "second@example.com")
        self.assertTrue(account["id"].startswith("gmail-"))
        dialog.close(); window.close()

    @patch("desktop.gmail_service")
    def test_auth_uses_selected_account_identity(self, service):
        account = {"id": "second", "label": "Second", "email": "second@example.com"}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = authorization_helper(self.store.credentials, account["id"], account["email"])
        service.assert_called_once_with(self.store.credentials, authorize=True, account_id="second", expected_email="second@example.com")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), {"authorized": "second"})

    def test_cancel_authorization_stops_blocked_helper_and_restores_controls(self):
        window = MainWindow(self.store)
        worker = Worker(self.store, self.store.config["gmail_accounts"][0], 10, authorize=True)
        started = threading.Event()
        processes, commands = [], []
        real_popen = subprocess.Popen
        def blocked_helper(command, **kwargs):
            commands.append(command)
            process = real_popen([os.sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
            processes.append(process); started.set()
            return process
        with patch("desktop.subprocess.Popen", side_effect=blocked_helper):
            window.start_worker(worker)
            self.assertTrue(started.wait(2))
            cancelled_at = time.monotonic()
            window.cancel()
            while window.worker and time.monotonic() - cancelled_at < 2.5:
                APP.processEvents(); time.sleep(0.01)
            self.assertIsNone(window.worker)
        self.assertLess(time.monotonic() - cancelled_at, 2.5)
        self.assertIsNotNone(processes[0].poll())
        self.assertTrue(window.scan_button.isEnabled())
        self.assertTrue(window.authorize_button.isEnabled())
        self.assertTrue(window.settings_button.isEnabled())
        self.assertFalse(window.cancel_button.isEnabled())
        self.assertIn("Cancelled", window.status.text())
        self.assertIn("--authorize-helper", commands[0])
        self.assertIn(str(self.store.credentials), commands[0])
        window.close()

    def test_authorization_denial_returns_error_without_exposing_provider_details(self):
        output = io.StringIO()
        with patch("desktop.gmail_service", side_effect=Exception("private provider response")), contextlib.redirect_stdout(output):
            code = authorization_helper(self.store.credentials, "main", "main@example.com")
        self.assertEqual(code, 1)
        result = json.loads(output.getvalue())
        self.assertIn("denied", result["error"])
        self.assertNotIn("private provider response", result["error"])

    def test_packaged_authorization_helper_error_reaches_worker(self):
        worker = Worker(self.store, self.store.config["gmail_accounts"][0], 10, authorize=True)
        results, errors, commands = [], [], []
        worker.result.connect(results.append); worker.failed.connect(errors.append)
        real_popen = subprocess.Popen
        def denied_helper(command, **kwargs):
            commands.append(command)
            return real_popen([os.sys.executable, "-c", 'import json; print(json.dumps({"error": "Google access denied"})); raise SystemExit(1)'], **kwargs)
        with patch("desktop.subprocess.Popen", side_effect=denied_helper), patch.object(os.sys, "frozen", True, create=True):
            worker.run()
        self.assertEqual(results, [])
        self.assertEqual(errors, ["Google access denied"])
        self.assertEqual(commands[0][1], "--authorize-helper")

    @patch("desktop.gmail_service")
    def test_retrieval_failures_are_visible_and_mark_coverage_incomplete(self, service):
        service.return_value.users.return_value.getProfile.return_value.execute.return_value = {"emailAddress": "me@example.com"}
        def read(_service, days, limit, progress, failures):
            failures.append({"thread_id": "failed-id", "reason": "Could not retrieve"})
            return [], 1, False
        worker = Worker(self.store, self.store.config["gmail_accounts"][0], 10)
        results, statuses = [], []
        worker.result.connect(results.append)
        worker.progress.connect(lambda value, message: statuses.append(message))
        with patch("desktop.read_threads", side_effect=read):
            worker.run()
        self.assertEqual(results[0]["failed"][0]["thread_id"], "failed-id")
        self.assertIn("coverage is incomplete", statuses[-1])

    def test_cancelled_scan_does_not_publish_partial_results(self):
        worker = Worker(self.store, self.store.config["gmail_accounts"][0], 40, demo=True)
        results, errors = [], []
        worker.result.connect(results.append); worker.failed.connect(errors.append)
        with patch.object(worker, "isInterruptionRequested", return_value=True):
            worker.run()
        self.assertEqual(results, [])
        self.assertIn("Cancelled", errors[0])

    def test_mail_html_is_displayed_as_text(self):
        window = MainWindow(self.store, demo=True)
        worker = Worker(self.store, self.store.config["gmail_accounts"][0], 40, demo=True)
        results = []; worker.result.connect(results.append); worker.run()
        results[0]["notable"][0]["item"]["summary"] = '<img src="https://example.com/tracker"><script>bad()</script>'
        window.complete(results[0])
        self.assertIn('<img src="https://example.com/tracker">', window.detail.toPlainText())
        self.assertIn("<script>bad()</script>", window.detail.toPlainText())
        window.close()

    def test_multiple_accounts_scanned_sequentially_with_per_account_limits_and_origin(self):
        accounts = [{"id": "one", "label": "Personal", "email": "one@example.com"},
                    {"id": "two", "label": "Work", "email": "two@example.com"}]
        calls = []
        def connect(credentials, account_id, expected_email):
            calls.append(("connect", account_id))
            service = MagicMock()
            service.users.return_value.getProfile.return_value.execute.return_value = {"emailAddress": expected_email}
            return service
        def read(service, days, limit, progress, failures):
            calls.append(("read", limit))
            progress("Reading thread 1 of 1")
            return demo_threads()[:1], 0, False
        worker = Worker(self.store, accounts, 7)
        results, progress = [], []
        worker.result.connect(results.append)
        worker.progress.connect(lambda value, message: progress.append(value))
        with patch("desktop.gmail_service", side_effect=connect), patch("desktop.read_threads", side_effect=read):
            worker.run()
        self.assertEqual(calls, [("connect", "one"), ("read", 7), ("connect", "two"), ("read", 7)])
        result = results[0]
        self.assertEqual(len(result["notable"]), 2)
        self.assertEqual({e["account_email"] for e in result["notable"]}, {"one@example.com", "two@example.com"})
        for entry in result["notable"]:
            self.assertIn("authuser=" + entry["account_email"].replace("@", "%40"), entry["item"]["url"])
        self.assertEqual(progress, sorted(progress))
        self.assertEqual(progress[-1], 100)
        window = MainWindow(self.store)
        window.complete(result)
        self.assertIs(window.tabs.currentWidget(), window.summary)
        self.assertIn("one@example.com", window.summary.toPlainText())
        self.assertIn("two@example.com", window.summary.toPlainText())
        self.assertIn("Playtest invitation", window.summary.toPlainText())
        self.assertEqual(window.tables["notable"].topLevelItemCount(), 2)
        window.close()

    def test_failed_account_does_not_stop_remaining_accounts(self):
        accounts = [{"id": "broken", "label": "Broken", "email": "broken@example.com"},
                    {"id": "working", "label": "Working", "email": "working@example.com"}]
        service = MagicMock()
        service.users.return_value.getProfile.return_value.execute.return_value = {"emailAddress": "working@example.com"}
        worker = Worker(self.store, accounts, 2)
        results = []; worker.result.connect(results.append)
        with patch("desktop.gmail_service", side_effect=[RuntimeError("Authorize this Gmail first"), service]), patch("desktop.read_threads", return_value=(demo_threads()[:1], 0, False)):
            worker.run()
        result = results[0]
        self.assertEqual(result["failed"][0]["account_id"], "broken")
        self.assertEqual(result["notable"][0]["account_id"], "working")
        self.assertFalse(result["account_summaries"][0]["success"])
        self.assertTrue(result["account_summaries"][1]["success"])
        self.assertIn("1 of 2", result["status"])

    def test_editor_save_includes_typed_entry_and_cancel_preserves_original(self):
        original = ["mentor@example.com"]
        dialog = RuleListDialog(None, "Mentors", original, emails=True)
        dialog.input.setText(" New@Example.com ")
        dialog.save_and_close()
        self.assertEqual(dialog.values, ["mentor@example.com", "new@example.com"])
        self.assertEqual(original, ["mentor@example.com"])
        cancelled = RuleListDialog(None, "Mentors", original, emails=True)
        cancelled.input.setText("discard@example.com"); cancelled.add_entry(); cancelled.reject()
        self.assertEqual(cancelled.values, original)

    def test_list_editor_rejects_invalid_domains_and_duplicates(self):
        dialog = RuleListDialog(None, "Banks", ["bank.com"])
        for value in ("https://bank.com", "person@bank.com", "bank.com", "-invalid.com"):
            dialog.input.setText(value)
            self.assertFalse(dialog.add_entry())
        dialog.input.setText(" Cards.Bank.com ")
        self.assertTrue(dialog.add_entry())
        dialog.save_and_close()
        self.assertEqual(dialog.values, ["bank.com", "cards.bank.com"])

    def test_settings_cancel_discards_list_changes_and_save_persists_them(self):
        window = MainWindow(self.store)
        original = list(self.store.config["mentor_emails"])
        dialog = SettingsDialog(window)
        dialog.rule_values["mentor_emails"] = ["discard@example.com"]
        dialog.reject()
        self.assertEqual(self.store.config["mentor_emails"], original)
        saved = SettingsDialog(window)
        saved.rule_values["mentor_emails"] = ["saved@example.com"]
        self.assertTrue(saved.persist())
        self.assertEqual(Store(self.store.root).config["mentor_emails"], ["saved@example.com"])
        saved.close(); window.close()


if __name__ == "__main__":
    unittest.main()
