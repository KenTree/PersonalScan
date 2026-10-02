#!/usr/bin/env python3
"""Local-only dashboard. python3 app.py --demo requires only Python."""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import quote
import webbrowser
from scanner import DEFAULTS, demo_threads, digest_thread
from providers import gmail_service, read_threads, LocalAI

ROOT = Path(__file__).resolve().parent

def load_config(path):
    config = {**DEFAULTS}
    if path.exists(): config.update(json.loads(path.read_text()))
    for key in ("mentor_emails", "riot_domains", "bank_domains"):
        if not isinstance(config[key], list) or not all(isinstance(x, str) and x.strip() for x in config[key]):
            raise ValueError(f"{key} must be a list of non-empty strings.")
    for key, high in (("lookback_days", 365), ("max_threads", 150)):
        if type(config[key]) is not int or not 1 <= config[key] <= high:
            raise ValueError(f"{key} must be between 1 and {high}.")
    if not isinstance(config["model"], str): raise ValueError("model must be a string")
    return config

def main():
    parser = argparse.ArgumentParser(description="Personal Scanner for Gmail")
    parser.add_argument("--demo", action="store_true", help="Fictional examples; no Gmail calls")
    parser.add_argument("--authorize", action="store_true", help="Authorize read-only Gmail and exit")
    parser.add_argument("--forget-auth", action="store_true", help="Remove local Gmail authorization from Keychain")
    parser.add_argument("--ai", action="store_true", help="Use an owned cloud-disabled Ollama process")
    parser.add_argument("--model", help="Override config's local model name")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.model: config["model"] = args.model
    if args.forget_auth:
        import sys
        if sys.platform != "darwin": parser.error("This command uses macOS Keychain.")
        from keyring.backends.macOS import Keyring
        keychain = Keyring()
        if keychain.get_password("personal-scanner-gmail", "oauth"):
            keychain.delete_password("personal-scanner-gmail", "oauth")
        print("Local authorization removed. To revoke Google's grant, use your Google account's connected-app settings.")
        return
    if args.authorize:
        gmail_service(ROOT / "credentials.json", authorize=True)
        print("Gmail authorized with read-only access. Start with: python3 app.py")
        return
    ai = LocalAI(config["model"]) if args.ai else None
    lock = threading.Lock()
    state = dict(busy=False, status="Ready. Scan when you want a digest.", items=[], excluded=[],
                 last_scan=None, error=None, counts={}, demo=args.demo,
                 engine="Local AI" if ai else "Source excerpts", mentor_configured=bool(config["mentor_emails"]),
                 lookback_days=config["lookback_days"], max_threads=config["max_threads"], incomplete=False,
                 account_email=None)
    token = secrets.token_urlsafe(32)
    def progress(message):
        with lock: state["status"] = message
    def scan(max_threads):
        try:
            progress("Loading fictional examples…" if args.demo else "Connecting to Gmail…")
            effective = {**config}
            if args.demo:
                samples = demo_threads()
                threads, failures, capped = samples[:max_threads], 0, len(samples) > max_threads
                effective.update(mentor_emails=["contact@example.com"],
                                 riot_domains=["studio.example"],
                                 bank_domains=["bank.example", "otherbank.example"])
                account_email = None
            else:
                service = gmail_service(ROOT / "credentials.json")
                account_email = service.users().getProfile(userId="me").execute(num_retries=2)["emailAddress"]
                threads, failures, capped = read_threads(service,
                    config["lookback_days"], max_threads, progress)
            items, excluded = [], []
            ai_failures = 0
            for i, messages in enumerate(threads):
                progress(f"Evaluating thread {i+1} of {len(threads)}…")
                item, reason = digest_thread(messages, effective)
                if item:
                    if args.demo: item["url"] = None
                    else: item["url"] = "https://mail.google.com/mail/?authuser=" + quote(account_email, safe="") + "#all/" + quote(item["thread_id"], safe="")
                    if ai:
                        progress(f"Summarizing locally: {len(items)+1}…")
                        try: ai.summarize(item)
                        except Exception:
                            ai_failures += 1
                            item["warnings"].append("Local AI failed or returned unsupported evidence; source excerpts retained.")
                    item.pop("context")
                    items.append(item)
                elif messages:
                    latest = sorted(messages, key=lambda m: m.received)[-1]
                    excluded.append(dict(subject=latest.subject, sender=latest.sender, reason=reason))
            items.sort(key=lambda i: i["received"], reverse=True)
            notices = []
            if failures: notices.append(f"{failures} thread(s) could not be retrieved.")
            if capped: notices.append("Thread limit reached; older matching threads were not fetched. Increase max_threads to expand coverage.")
            if not args.demo and not config["mentor_emails"]: notices.append("The contact's address is not configured; mentor detection is inactive.")
            if ai_failures: notices.append(f"{ai_failures} AI summary attempt(s) used source excerpts instead.")
            with lock:
                state.update(items=items, excluded=excluded, last_scan=datetime.now(timezone.utc).isoformat(), account_email=account_email,
                    error=None, incomplete=bool(failures or capped),
                    status="Scan finished. " + " ".join(notices),
                    counts=dict(scanned=len(threads), notable=len(items), excluded=len(excluded), failed=failures))
        except Exception as error:
            # Controlled text for setup errors; do not expose mailbox/provider exceptions.
            message = str(error) if isinstance(error, RuntimeError) else "Scan failed. Check Gmail authorization and network access; see README.md."
            with lock: state.update(error=message, status="Scan failed; any previous results are stale.", incomplete=True)
        finally:
            with lock: state["busy"] = False

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def permitted_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"
        def send(self, status, body, content_type="application/json"):
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data)
        def do_GET(self):
            if not self.permitted_host(): return self.send(403, {"error": "Invalid host"})
            if self.path in ("/", "/ui.js", "/style.css"):
                filename, kind = {"/": ("index.html", "text/html"), "/ui.js": ("ui.js", "text/javascript"), "/style.css": ("style.css", "text/css")}[self.path]
                return self.send(200, (ROOT / "ui" / filename).read_text(), kind)
            if self.path == "/api/state":
                if not secrets.compare_digest(self.headers.get("X-Scanner-Token", ""), token):
                    return self.send(403, {"error": "Open the full dashboard URL printed in Terminal."})
                with lock: snapshot = json.loads(json.dumps(state))
                return self.send(200, snapshot)
            self.send(404, {"error": "Not found"})
        def do_POST(self):
            if not self.permitted_host(): return self.send(403, {"error": "Invalid host"})
            expected = f"http://127.0.0.1:{self.server.server_port}"
            if self.headers.get("Origin") not in (None, expected) or not secrets.compare_digest(self.headers.get("X-Scanner-Token", ""), token):
                return self.send(403, {"error": "Request blocked"})
            if self.path != "/api/scan": return self.send(404, {"error": "Not found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 1024:
                    raise ValueError()
                payload = json.loads(self.rfile.read(length)) if length else {}
                if not isinstance(payload, dict):
                    raise ValueError()
                max_threads = payload.get("max_threads", config["max_threads"])
                if type(max_threads) is not int or not 1 <= max_threads <= 150:
                    raise ValueError()
            except (ValueError, UnicodeDecodeError):
                return self.send(400, {"error": "Thread limit must be a whole number from 1 to 150."})
            with lock:
                if state["busy"]: return self.send(409, {"error": "A scan is already running"})
                state.update(busy=True, error=None, max_threads=max_threads, status="Starting scan…")
            threading.Thread(target=scan, args=(max_threads,), daemon=True).start()
            self.send(202, {"status": "started"})

    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
        server.daemon_threads = True
        url = f"http://127.0.0.1:{server.server_port}/#token={token}"
        print("Personal Scanner · " + ("fictional demo" if args.demo else "live Gmail"))
        print(url, flush=True)
        print("Keep this Terminal open. Press Ctrl+C to stop. Results stay in memory.")
        if not args.no_browser: webbrowser.open(url)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close()
    finally:
        if ai: ai.close()

if __name__ == "__main__":
    try: main()
    except (RuntimeError, ValueError) as error: raise SystemExit(str(error))
