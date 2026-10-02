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
    def call(self, path, method="GET", headers=None):
        request = urllib.request.Request(self.base+path, data=b"" if method=="POST" else None,
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
    def test_privacy_boundaries(self):
        self.assertEqual(self.call("/api/state")[0], 403)
        self.assertEqual(self.call("/api/state", headers={"Host":"evil.example", "X-Scanner-Token":self.token})[0], 403)
        self.assertEqual(self.call("/api/scan", "POST", {"Origin":"https://evil.example", "X-Scanner-Token":self.token})[0], 403)
        self.assertEqual(self.call("/credentials.json")[0], 404)

if __name__ == "__main__": unittest.main()
