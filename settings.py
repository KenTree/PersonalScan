"""Validated settings shared by the desktop app and legacy command-line tools."""
import json
import re
from scanner import DEFAULTS

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
