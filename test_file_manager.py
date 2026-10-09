import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from desktop import Store
from file_manager import ReviewCancelled, analyze_batch, inventory, review_files
from file_manager_ui import FileManagerDialog, FileReviewWorker

APP = QApplication.instance() or QApplication([])


class FileManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "Downloads"
        self.root.mkdir()
        self.now = time.time()

    def tearDown(self):
        self.temp.cleanup()

    def file(self, name, age=200):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture contents must not be read by inventory")
        timestamp = self.now - age * 86400
        os.utime(path, (timestamp, timestamp))
        return path

    def test_inventory_finds_old_candidates_without_touching_contents(self):
        screenshot = self.file("Screenshot 2025-01-01.png")
        school = self.file("assignment1.pdf")
        self.file("new-assignment.pdf", age=2)
        self.file("invoice.pdf"); self.file("client_secret.json")
        self.file(".private/homework.pdf"); self.file("Application.app/lecture.pdf")
        self.file("node_modules/course.pdf")
        self.file("photoslibrary.photoslibrary/Screenshot.png")
        self.file("private.key")
        before = screenshot.stat()
        with patch.object(Path, "read_text", side_effect=AssertionError("File contents must not be read")):
            result = inventory([self.root], now=self.now)
        self.assertEqual({e["name"] for e in result["candidates"]}, {screenshot.name, school.name})
        self.assertEqual(screenshot.stat().st_mtime, before.st_mtime)
        self.assertEqual(screenshot.stat().st_atime, before.st_atime)
        self.assertEqual(result["errors"], [])

    def test_links_cannot_extend_selected_folder_and_overlapping_roots_deduplicate(self):
        candidate = self.file("course/homework.pdf")
        outside = Path(self.temp.name) / "outside"; outside.mkdir()
        (outside / "assignment.pdf").write_text("outside")
        (self.root / "linked").symlink_to(outside, target_is_directory=True)
        (self.root / "linked.pdf").symlink_to(outside / "assignment.pdf")
        result = inventory([self.root / "course", self.root, self.root], now=self.now)
        self.assertEqual([e["path"] for e in result["candidates"]], [str(candidate.resolve())])

    def test_inventory_limits_and_missing_folders_are_disclosed(self):
        for i in range(5): self.file(f"assignment{i}.pdf", age=100 + i)
        result = inventory([self.root], now=self.now, max_files=2, max_candidates=1)
        self.assertEqual(result["checked"], 2)
        self.assertTrue(result["inventory_limited"])
        self.assertTrue(result["analysis_limited"])
        missing = inventory([self.root / "missing"], now=self.now)
        self.assertEqual(len(missing["errors"]), 1)

    def candidate(self):
        self.file("assignment.pdf")
        return inventory([self.root], now=self.now)["candidates"][0]

    def ai(self, items):
        ai = MagicMock(); ai.model = "local-test"
        ai.request.return_value = {"message": {"content": json.dumps({"files": items})}}
        return ai

    def decision(self, candidate, **changes):
        return {"id": candidate["id"], "decision": "review", "reason": "Older assignment may be worth reviewing; current usefulness is unknown.",
                "evidence": ["old_modified", "school_name"], **changes}

    def test_model_uses_only_metadata_and_cannot_introduce_file_paths(self):
        candidate = self.candidate()
        ai = self.ai([self.decision(candidate)])
        reviewed = analyze_batch(ai, [candidate])
        self.assertEqual(reviewed[0]["path"], candidate["path"])
        payload = ai.request.call_args.args[1]
        data = json.loads(payload["messages"][1]["content"])[0]
        self.assertNotIn("path", data)
        self.assertNotIn("body", data)
        self.assertNotIn("fixture contents", json.dumps(payload))
        self.assertIsInstance(payload["format"], dict)
        with self.assertRaises(ValueError):
            analyze_batch(self.ai([self.decision(candidate, id="/arbitrary/file")]), [candidate])

    def test_unsupported_evidence_and_incomplete_responses_are_not_recommendations(self):
        candidate = self.candidate()
        for values in ([], [self.decision(candidate, evidence=["never_used"])], [self.decision(candidate, evidence=["school_name"])],
                       [self.decision(candidate), self.decision(candidate)]):
            with self.assertRaises(ValueError): analyze_batch(self.ai(values), [candidate])

    def test_failed_ai_reviews_are_visible_and_files_are_preserved(self):
        candidate = self.candidate()
        ai = MagicMock(); ai.request.side_effect = ValueError("invalid model output")
        statuses = []
        with patch("file_manager.LocalAI", return_value=ai):
            result = review_files([self.root], "test-model", 90, 40, lambda value, message: statuses.append(message), lambda: False)
        self.assertEqual(result["review"], [])
        self.assertEqual(len(result["analysis_errors"]), 1)
        self.assertTrue(Path(candidate["path"]).exists())
        ai.close.assert_called_once()
        self.assertIn("AI review failures", statuses[-1])

    def test_cancel_interrupts_inventory_and_always_closes_model(self):
        self.file("Screenshot old.png")
        with self.assertRaises(ReviewCancelled): inventory([self.root], cancelled=lambda: True)
        ai = MagicMock()
        cancellation = [False]
        def cancel_inference(*args):
            cancellation[0] = True
            raise OSError("Model request interrupted")
        ai.request.side_effect = cancel_inference
        with patch("file_manager.LocalAI", return_value=ai), self.assertRaises(ReviewCancelled):
            review_files([self.root], "test", 90, 40, lambda *args: None, lambda: cancellation[0])
        ai.close.assert_called_once()

    def test_native_results_display_model_reason_and_metadata_as_text(self):
        candidate = self.candidate()
        entry = {**candidate, **self.decision(candidate, reason='<script>untrusted</script>')}
        store = Store(Path(self.temp.name) / "settings")
        dialog = FileManagerDialog(None, store)
        result = {"review": [entry], "kept": [], "errors": [], "analysis_errors": [], "checked": 1,
                  "inventory_limited": False, "analysis_limited": False, "review_bytes": candidate["size"]}
        dialog.complete(result)
        self.assertEqual(dialog.tables["review"].topLevelItemCount(), 1)
        self.assertIn("<script>untrusted</script>", dialog.detail.toPlainText())
        self.assertIn("Submission status is unknown", dialog.detail.toPlainText())
        with patch("file_manager_ui.QProcess.startDetached") as reveal:
            dialog.reveal()
            reveal.assert_called_once_with("/usr/bin/open", ["-R", candidate["path"]])
        dialog.close()

    def test_worker_cancel_stops_owned_model_process(self):
        worker = FileReviewWorker([self.root], "test", 90, 40)
        ai = MagicMock(); ai.process.poll.return_value = None; worker.ai_ready(ai)
        worker.cancel()
        ai.process.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
