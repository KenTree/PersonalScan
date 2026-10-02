"""Selection rules, Gmail decoding, and grounded local excerpts. No network calls."""
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parseaddr
from html.parser import HTMLParser
import re

DEFAULTS = dict(mentor_emails=[], riot_domains=["studio.example"],
                bank_domains=["bank.example", "cards.bank.example", "otherbank.example"],
                lookback_days=14, max_threads=150, model="qwen2.5:3b")

@dataclass
class Mail:
    id: str
    thread_id: str
    sender: str
    subject: str
    body: str
    received: str
    unread: bool = False
    attachments: list = field(default_factory=list)
    snippet_only: bool = False
    sent: bool = False

class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__(); self.text = []; self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"): self.hidden += 1
        if tag in ("p", "br", "div", "li", "tr"): self.text.append("\n")
    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"): self.hidden = max(0, self.hidden - 1)
    def handle_data(self, data):
        if not self.hidden: self.text.append(data)

def clean_html(value):
    parser = TextHTML(); parser.feed(value)
    return "".join(parser.text)

def normalize(value):
    return re.sub(r"\s+", " ", value).strip()

def decode_message(raw):
    payload = raw.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    plain, html, attachments = [], [], []
    def visit(part):
        filename = part.get("filename")
        if filename:
            attachments.append(filename)
            return
        data = part.get("body", {}).get("data")
        if data:
            encoded = data + "=" * (-len(data) % 4)
            value = base64.urlsafe_b64decode(encoded).decode("utf-8", errors="replace")
            if part.get("mimeType") == "text/plain": plain.append(value)
            elif part.get("mimeType") == "text/html": html.append(clean_html(value))
        for child in part.get("parts", []): visit(child)
    visit(payload)
    body = "\n".join(plain or html)
    snippet_only = not bool(body.strip())
    labels = raw.get("labelIds", [])
    received = datetime.fromtimestamp(int(raw.get("internalDate", "0"))/1000,
                                      timezone.utc).isoformat()
    return Mail(raw["id"], raw.get("threadId", raw["id"]), headers.get("from", ""),
                headers.get("subject", "(No subject)"), body or raw.get("snippet", ""),
                received, "UNREAD" in labels, attachments, snippet_only, "SENT" in labels)

def sender_address(sender):
    return parseaddr(sender)[1].lower()

def domain_matches(address, domains):
    domain = address.rsplit("@", 1)[-1]
    return any(domain == d.lower() or domain.endswith("." + d.lower()) for d in domains)

def match(pattern, text):
    return bool(re.search(pattern, text, re.I))

def classify(mail, config):
    """Return category + review flag + reason, or an explicit exclusion reason.

    Sender domains are relevance signals, NOT proof of sender authenticity.
    Ambiguous application-related mail is retained for review, never inferred as a status update.
    """
    if mail.sent: return None, False, "Outgoing message; retained only as thread context."
    address = sender_address(mail.sender)
    subject = normalize(mail.subject)
    # Avoid old quoted mail activating filters on a later message.
    body = re.split(r"(?im)^\s*(?:On .{5,200} wrote:|-----Original Message-----|From:)", mail.body)[0]
    text = subject + " " + normalize(body)
    if address and address in [e.lower() for e in config["mentor_emails"]]:
        return "Mentor", False, "Exact match to a configured mentor address."
    if domain_matches(address, config["riot_domains"]) and match(r"play[ -]?test", text):
        return "Playtest", False, "Configured game studio sender domain and playtest reference."
    if domain_matches(address, config["bank_domains"]):
        promotional = match(r"pre[ -]?(?:approved|qualified)|apply (?:now|today|for)|new card|\bcard offer\b|you(?:'re| are) eligible|see if you qualify", subject + " " + normalize(body[:800]))
        transactional = match(r"statement|payment (?:due|received|posted|scheduled|reminder)|transaction|purchase|fraud|security alert|verification code|account alert|balance alert|credit limit (?:changed|increased|decreased)|autopay|automatic payment", text)
        if promotional and not match(r"statement|payment (?:due|received|posted)|fraud|security alert|transaction|account alert", subject):
            return None, False, "Credit-card solicitation excluded."
        if transactional:
            return "Credit card", False, "Configured bank sender and statement/account activity."
        return None, False, "No statement or account notification detected."
    # Newsletter/job-board language wins over application-related marketing copy.
    job_board = match(r"job alerts?|jobs? (?:for you|recommended|digest)|recommended (?:jobs?|roles)|career newsletter|new (?:jobs?|opportunities)|hiring newsletter|jobs? you may|weekly (?:jobs?|opportunities)|apply to these|matches your (?:profile|skills)", text)
    if job_board: return None, False, "Job-board/newsletter content excluded."
    application = match(r"\b(?:your|the|my) application\b|\bapplication (?:status|update|received)\b|applied (?:for|to)|thank you for (?:applying|your interest)|candidate|interview|assessment|offer (?:letter|of employment)", text)
    update = match(r"application.{0,60}(?:received|status|update|review|progress)|thank you for applying|not (?:be )?(?:moving|proceeding)|unfortunately|interview|assessment|offer (?:letter|of employment)|selected for|next (?:step|round)|position.{0,40}(?:filled|closed)", text)
    if application and update:
        return "Application update", False, "Application/interview reference with a status or next-step signal."
    if application:
        return "Application update", True, "Possible application correspondence; confirm it relates to your application."
    return None, False, "Outside the configured categories."

IMPORTANT = r"due|deadline|by |confirm|schedule|interview|assessment|statement|payment|unfortunately|received|please|playtest|cancel|reschedul|no longer|resolved"

def excerpts(body):
    sentences = [normalize(s) for s in re.split(r"(?<=[.!?])\s+|\n+", body) if normalize(s)]
    chosen = [s for s in sentences if match(IMPORTANT, s)] or sentences[:2]
    return [s[:700] for s in chosen[:3]]

def digest_thread(messages, config):
    messages = sorted(messages, key=lambda m: m.received)
    matches = [(m, classify(m, config)) for m in messages]
    included = [(m, result) for m, result in matches if result[0]]
    if not included:
        return None, matches[-1][1][2] if matches else "Empty thread."
    selected, (category, review, reason) = included[-1]
    latest = messages[-1]
    context = "\n\n".join(f"From: {m.sender}\nDate: {m.received}\nSubject: {m.subject}\n{m.body}" for m in messages)
    # Always display the latest message, even if it does not repeat the original keywords.
    quotes = excerpts(latest.body)
    warnings = []
    if any(m.attachments for m in messages): warnings.append("Attachments listed only; their contents were not read.")
    if any(m.snippet_only for m in messages): warnings.append("One or more messages have only a preview, not their full body.")
    if latest.id != selected.id:
        warnings.append("Later thread message present; latest excerpts below may resolve or change the original request.")
    return dict(thread_id=selected.thread_id, category=category, review=review,
                reason=reason, subject=latest.subject, sender=latest.sender,
                received=latest.received, unread=any(m.unread for m in messages),
                excerpts=quotes, summary=" ".join(quotes), summary_type="Source excerpts",
                action_quote="", warnings=warnings, context=context,
                messages=[dict(sender=m.sender, subject=m.subject, received=m.received,
                               body=m.body, attachments=m.attachments, sent=m.sent) for m in messages],
                url="https://mail.google.com/mail/u/0/#all/" + selected.thread_id), None

def demo_threads():
    """Fictional demo messages using reserved example domains."""
    examples = [
        ("Playtest Team <playtest@studio.example>", "Playtest invitation", "You are invited to an example game studio playtest. Please confirm your availability by Friday.", False),
        ("Recruiting <careers@example.com>", "Update on your application", "Thank you for applying. We would like to schedule an interview. Please reply with your availability.", False),
        ("Recruiting <jobs@example.org>", "Application status", "Thank you for applying. Unfortunately, we are not moving forward with your application.", False),
        ("Demo Contact <contact@example.com>", "Checking in", "How is your project going? Please send me your progress when you have a chance.", False),
        ("Example Bank <statements@bank.example>", "Your statement is ready", "Your monthly statement is available. Please review the payment due date in your account.", False),
        ("Example Bank <alerts@otherbank.example>", "Payment received", "Your payment was received. No additional action is required for this notification.", False),
        ("Job Board <digest@example.net>", "Recommended jobs for you", "Here are new jobs for you. Apply to these positions and track your application status.", False),
        ("Example Bank <offers@otherbank.example>", "You're pre-approved for a new card", "Apply now for a new card and earn a welcome bonus.", False),
        ("Example Bank <offers@bank.example>", "See if you qualify", "You may be eligible for a new card. Apply today.", False),
        ("Gaming News <news@studio.example>", "This week's updates", "Read our latest game announcements.", False),
    ]
    result = [[Mail(f"demo{i}", f"demo{i}", sender, subject, body,
                    "2026-10-02T16:00:00+00:00", True, sent=sent)]
              for i, (sender, subject, body, sent) in enumerate(examples)]
    result[1].append(Mail("demo1b", "demo1", "Recruiting <careers@example.com>",
                        "Re: Update on your application", "The interview has been rescheduled. Please disregard the earlier invitation; we will send new times.",
                        "2026-10-02T17:00:00+00:00", True))
    return result
