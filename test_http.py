"""Integration checks against a real localhost demo server, without Gmail access."""
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

class DashboardHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proc = subprocess.Popen([sys.executable, "app.py", "--demo", "--no-browser", "--port", "0"],
                                    cwd=Path(__file__).resolve().parent, stdout=subprocess.PIPE, text=True)
        cls.proc.stdout.readline()
        cls.base, cls.token = cls.proc.stdout.readline().strip().split("/#token=")
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(timeout=5); cls.proc.stdout.close()
    def call(self, path, method="GET", headers=None, payload=None):
        request = urllib.request.Request(self.base+path, data=(json.dumps(payload).encode() if payload is not None else b"") if method=="POST" else None,
                                         headers=headers or {})
        try:
            with self.opener.open(request, timeout=5) as response: return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
    def test_demo_digest(self):
        self.assertEqual(self.call("/")[0], 200)
        self.assertEqual(self.call("/api/scan", "POST", {"X-Scanner-Token":self.token})[0], 202)
        for _ in range(100):
            status, body = self.call("/api/state", headers={"X-Scanner-Token":self.token})
            state = json.loads(body)
            if not state["busy"]: break
            time.sleep(.02)
        self.assertEqual(status, 200)
        self.assertEqual(state["counts"], dict(scanned=10, notable=6, excluded=4, failed=0))
        self.assertIsNone(state["error"])
        self.assertTrue(all(item["url"] is None for item in state["items"]))
    def test_thread_limit_validation(self):
        headers = {"X-Scanner-Token": self.token, "Content-Type": "application/json"}
        for value in (0, -1, 151, 2.5, True, "40", None):
            with self.subTest(value=value):
                self.assertEqual(self.call("/api/scan", "POST", headers, {"max_threads": value})[0], 400)
        for limit in (1, 150):
            self.assertEqual(self.call("/api/scan", "POST", headers, {"max_threads": limit})[0], 202)
            for _ in range(100):
                _, body = self.call("/api/state", headers=headers)
                state = json.loads(body)
                if not state["busy"]: break
                time.sleep(.02)
            self.assertFalse(state["busy"])
            self.assertEqual(state["max_threads"], limit)
            self.assertEqual(state["counts"]["scanned"], min(limit, 10))
            self.assertEqual(state["incomplete"], limit < 10)

    def test_account_selection_clears_previous_digest(self):
        headers = {"X-Scanner-Token": self.token}
        for bad in ("unknown", 4, {}, None):
            self.assertEqual(self.call("/api/account", "POST", headers, {"account_id": bad})[0], 400)
        self.assertEqual(self.call("/api/scan", "POST", headers, {"account_id": "unknown"})[0], 400)
        self.assertEqual(self.call("/api/account", "POST", headers, {"account_id": "demo-personal"})[0], 200)
        self.assertEqual(self.call("/api/scan", "POST", headers, {"max_threads": 1})[0], 202)
        for _ in range(100):
            _, body = self.call("/api/state", headers=headers)
            if not json.loads(body)["busy"]: break
            time.sleep(.02)
        self.assertEqual(self.call("/api/account", "POST", headers, {"account_id": "demo-work"})[0], 200)
        _, body = self.call("/api/state", headers=headers)
        state = json.loads(body)
        self.assertEqual(state["selected_account"], "demo-work")
        self.assertEqual(state["items"], [])
        self.assertEqual(state["excluded"], [])
        self.assertEqual(state["counts"], {})
        self.assertIsNone(state["last_scan"])
        self.assertIsNone(state["account_email"])
        self.assertFalse(state["incomplete"])

    def test_built_frontend_assets(self):
        import re
        status, body = self.call("/")
        self.assertEqual(status, 200)
        assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', body.decode())
        self.assertGreaterEqual(len(assets), 2)
        for asset in assets:
            self.assertEqual(self.call(asset)[0], 200)
        for path in ("/assets/../../config.json", "/assets/../index.html", "/src/main.jsx", "/package.json"):
            self.assertEqual(self.call(path)[0], 404)

    def test_privacy_boundaries(self):
        self.assertEqual(self.call("/api/state")[0], 403)
        self.assertEqual(self.call("/api/account", "POST", payload={"account_id": "demo-work"})[0], 403)
        self.assertEqual(self.call("/api/state", headers={"Host":"evil.example", "X-Scanner-Token":self.token})[0], 403)
        self.assertEqual(self.call("/api/scan", "POST", {"Origin":"https://evil.example", "X-Scanner-Token":self.token})[0], 403)
        self.assertEqual(self.call("/credentials.json")[0], 404)

if __name__ == "__main__": unittest.main()
