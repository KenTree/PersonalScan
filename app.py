#!/usr/bin/env python3
"""Local-only API and server for the locally built React dashboard."""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import re
import threading
from urllib.parse import quote
import webbrowser
from scanner import DEFAULTS, demo_threads, digest_thread
from providers import gmail_service, read_threads, LocalAI, SERVICE, auth_key

ROOT = Path(__file__).resolve().parent
UI_ROOT = ROOT / "ui" / "dist"

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
    accounts = config.get("gmail_accounts", [{"id": "default", "label": "My Gmail", "email": ""}])
    if not isinstance(accounts, list) or not accounts:
        raise ValueError("gmail_accounts must be a non-empty list.")
    seen = set()
    for account in accounts:
        if not isinstance(account, dict):
            raise ValueError("Each Gmail account must contain id, label, and email fields.")
        account_id, label, email = (account.get(key, "") for key in ("id", "label", "email"))
        if not isinstance(account_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", account_id) or account_id in seen:
            raise ValueError("Gmail account IDs must be unique and use only letters, numbers, underscores, or hyphens.")
        if not isinstance(label, str) or not label.strip() or len(label) > 100:
            raise ValueError("Gmail account labels must be non-empty strings up to 100 characters.")
        if not isinstance(email, str) or (email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)) or (not email and account_id != "default"):
            raise ValueError("Each Gmail account needs an email address; only the legacy default account may leave it blank.")
        seen.add(account_id)
    config["gmail_accounts"] = [{"id": account["id"], "label": account["label"], "email": account.get("email", "")} for account in accounts]
    return config

def main():
    parser = argparse.ArgumentParser(description="Personal Scanner for Gmail")
    parser.add_argument("--demo", action="store_true", help="Fictional examples; no Gmail calls")
    parser.add_argument("--authorize", action="store_true", help="Authorize read-only Gmail and exit")
    parser.add_argument("--forget-auth", action="store_true", help="Remove local Gmail authorization from Keychain")
    parser.add_argument("--account", help="Account ID from local config.json for authorization/removal")
    parser.add_argument("--ai", action="store_true", help="Use an owned cloud-disabled Ollama process")
    parser.add_argument("--model", help="Override config's local model name")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.model: config["model"] = args.model
    accounts = config["gmail_accounts"]
    account_id = args.account or accounts[0]["id"]
    account = next((item for item in accounts if item["id"] == account_id), None)
    if account is None: parser.error("Unknown account ID. Add it to gmail_accounts in your local config.json.")
    if args.forget_auth:
        import sys
        if sys.platform != "darwin": parser.error("This command uses macOS Keychain.")
        from keyring.backends.macOS import Keyring
        keychain = Keyring()
        if keychain.get_password(SERVICE, auth_key(account_id)):
            keychain.delete_password(SERVICE, auth_key(account_id))
        print("Local authorization removed. To revoke Google's grant, use your Google account's connected-app settings.")
        return
    if args.authorize:
        gmail_service(ROOT / "credentials.json", authorize=True, account_id=account_id, expected_email=account["email"])
        print("Gmail authorized with read-only access. Start with: python3 app.py")
        return
    if not (UI_ROOT / "index.html").is_file():
        raise RuntimeError("Build the React dashboard first: npm install && npm run build")
    ai = LocalAI(config["model"]) if args.ai else None
    if args.demo:
        accounts = [{"id": "demo-personal", "label": "Demo Personal", "email": "personal@example.com"},
                    {"id": "demo-work", "label": "Demo Work", "email": "work@example.com"}]
        account_id = accounts[0]["id"]
    accounts_by_id = {item["id"]: item for item in accounts}
    lock = threading.Lock()
    state = dict(busy=False, status="Ready. Scan when you want a digest.", items=[], excluded=[],
                 last_scan=None, error=None, counts={}, demo=args.demo,
                 engine="Local AI" if ai else "Source excerpts", mentor_configured=bool(config["mentor_emails"]),
                 lookback_days=config["lookback_days"], max_threads=config["max_threads"], incomplete=False,
                 account_email=None, accounts=accounts, selected_account=account_id)
    token = secrets.token_urlsafe(32)
    def progress(message):
        with lock: state["status"] = message
    def scan(max_threads, selected_account):
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
                service = gmail_service(ROOT / "credentials.json", account_id=selected_account,
                                        expected_email=accounts_by_id[selected_account]["email"])
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
            if self.path == "/":
                return self.send(200, (UI_ROOT / "index.html").read_text(), "text/html")
            if self.path.startswith("/assets/"):
                asset = (UI_ROOT / self.path.lstrip("/")).resolve()
                kinds = {".js": "text/javascript", ".css": "text/css"}
                if asset.is_relative_to((UI_ROOT / "assets").resolve()) and asset.is_file() and asset.suffix in kinds:
                    return self.send(200, asset.read_text(), kinds[asset.suffix])
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
            if self.path not in ("/api/scan", "/api/account"): return self.send(404, {"error": "Not found"})
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
            selected_account = payload.get("account_id")
            if selected_account is not None and (not isinstance(selected_account, str) or selected_account not in accounts_by_id):
                return self.send(400, {"error": "Choose a configured Gmail account."})
            if self.path == "/api/account" and selected_account is None:
                return self.send(400, {"error": "Choose a configured Gmail account."})
            with lock:
                if state["busy"]: return self.send(409, {"error": "A scan is already running"})
                selected_account = selected_account or state["selected_account"]
                if selected_account != state["selected_account"]:
                    state.update(items=[], excluded=[], counts={}, last_scan=None, account_email=None,
                                 error=None, incomplete=False, selected_account=selected_account,
                                 status="Account selected. Scan when you want a digest.")
                if self.path == "/api/account":
                    return self.send(200, {"selected_account": selected_account})
                state.update(busy=True, error=None, max_threads=max_threads, status="Starting scan…")
            threading.Thread(target=scan, args=(max_threads, selected_account), daemon=True).start()
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
