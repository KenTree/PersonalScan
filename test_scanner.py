import base64
import unittest
from unittest.mock import patch
from scanner import DEFAULTS, Mail, classify, decode_message, demo_threads, digest_thread
from providers import LocalAI, read_threads

class Filters(unittest.TestCase):
    def setUp(self): self.config = {**DEFAULTS, "mentor_emails": ["contact@mentor.example"]}
    def mail(self, sender, subject, body="", **kwargs):
        return Mail("1", "t1", sender, subject, body, "2026-10-02T12:00:00+00:00", **kwargs)
    def category(self, sender, subject, body=""):
        return classify(self.mail(sender, subject, body), self.config)[0]
    def test_riot_playtest(self): self.assertEqual(self.category("p@studio.example", "Playtest invitation"), "Playtest")
    def test_riot_subdomain(self): self.assertEqual(self.category("p@email.studio.example", "Play-test reminder"), "Playtest")
    def test_riot_other_news(self): self.assertIsNone(self.category("p@studio.example", "Game update"))
    def test_lookalike_domain(self): self.assertIsNone(self.category("p@studio.example.evil.example", "Playtest"))
    def test_mentor_exact(self): self.assertEqual(self.category("Contact <contact@mentor.example>", "Hello"), "Mentor")
    def test_mentor_display_name_insufficient(self): self.assertIsNone(self.category("Contact <other@example.com>", "Hello"))
    def test_application_acknowledgment(self): self.assertEqual(self.category("jobs@example.com", "Thank you for applying"), "Application update")
    def test_interview(self): self.assertEqual(self.category("hiring@example.com", "Interview invitation", "Please schedule your interview."), "Application update")
    def test_application_subject(self): self.assertEqual(self.category("hiring@example.com", "Application received", "We will get back to you shortly."), "Application update")
    def test_rejection(self): self.assertEqual(self.category("hr@example.com", "Your application", "Unfortunately we are not moving forward."), "Application update")
    def test_job_board(self): self.assertIsNone(self.category("jobs@example.com", "Recommended jobs for you", "Track your application status."))
    def test_newsletter(self): self.assertIsNone(self.category("jobs@example.com", "Career newsletter", "Your application can stand out in an interview."))
    def test_card_statement(self): self.assertEqual(self.category("s@bank.example", "Your statement is ready"), "Credit card")
    def test_bank_alert(self): self.assertEqual(self.category("a@notify.otherbank.example", "Payment received"), "Credit card")
    def test_card_offer(self): self.assertIsNone(self.category("o@bank.example", "You're pre-approved", "Apply for a new card."))
    def test_card_offer_payment_marketing(self): self.assertIsNone(self.category("o@otherbank.example", "New card offer", "Apply now and lower your payment due each month."))
    def test_possible_application_review(self): self.assertTrue(classify(self.mail("hr@example.com", "Regarding your application", "Can we talk?"), self.config)[1])
    def test_outgoing_only_excluded(self): self.assertIsNone(classify(self.mail("me@example.com", "Your application", sent=True), self.config)[0])
    def test_quoted_old_request_not_new_match(self):
        self.assertIsNone(self.category("x@example.com", "Hello", "Thanks.\nOn Monday someone wrote:\nYour application status: interview"))
    def test_latest_reply_changes_digest(self):
        older=self.mail("hr@example.com", "Interview", "Please schedule an interview.")
        newer=Mail("2","t1","hr@example.com","Re: Interview","The interview is cancelled. Please disregard the earlier request.","2026-10-02T13:00:00+00:00")
        item,_=digest_thread([older,newer],self.config)
        self.assertIn("cancelled",item["summary"])
        self.assertEqual(len(item["messages"]),2)
    def test_demo(self):
        config={**DEFAULTS,"mentor_emails":["contact@example.com"]}
        result=[digest_thread(t,config)[0] for t in demo_threads()]
        self.assertEqual(sum(bool(i) for i in result),6)
    def test_html_not_remote_fetch(self):
        text='<p>Payment received.</p><img src="https://tracker.example/x"><script>bad()</script>'
        raw={"id":"1","payload":{"mimeType":"text/html","body":{"data":base64.urlsafe_b64encode(text.encode()).decode()},"headers":[]}}
        mail=decode_message(raw)
        self.assertIn("Payment received",mail.body)
        self.assertNotIn("bad()",mail.body)
        self.assertNotIn("tracker",mail.body)
    def test_attachment_flag(self):
        item,_=digest_thread([self.mail("s@bank.example","Statement", "Read the attached statement.",attachments=["statement.pdf"])],self.config)
        self.assertTrue(item["warnings"])

class LocalSummary(unittest.TestCase):
    def engine(self, result):
        ai=LocalAI.__new__(LocalAI);ai.model="local";ai.request=lambda *args: {"message":{"content":__import__('json').dumps(result)}}
        return ai
    def item(self): return {"context":"Your payment was received. No action is required.","warnings":[]}
    def test_cloud_model_blocked_before_start(self):
        with self.assertRaises(RuntimeError): LocalAI("example:cloud")
    def test_grounded_summary(self):
        item=self.item();self.engine({"summary":"Payment received.","evidence":["Your payment was received."],"action_quote":""}).summarize(item)
        self.assertIn("Local AI",item["summary_type"])
    def test_invented_evidence_rejected(self):
        with self.assertRaises(ValueError): self.engine({"summary":"Pay $100 tomorrow.","evidence":["Pay $100 tomorrow."],"action_quote":""}).summarize(self.item())
    def test_invented_action_rejected(self):
        with self.assertRaises(ValueError): self.engine({"summary":"Received.","evidence":["Your payment was received."],"action_quote":"Pay now"}).summarize(self.item())
    def test_old_action_rejected(self):
        item=self.item();item["context"]+=" Please pay tomorrow.";item["messages"]=[{"body":"No action is required."}]
        with self.assertRaises(ValueError): self.engine({"summary":"Received.","evidence":["Your payment was received."],"action_quote":"Please pay tomorrow."}).summarize(item)
    def test_long_context_no_truncation(self):
        item={"context":"x"*16001,"warnings":[]};self.engine({}).summarize(item);self.assertTrue(item["warnings"])

class Pagination(unittest.TestCase):
    def test_pagination_and_thread_failure(self):
        from unittest.mock import MagicMock
        service=MagicMock();api=service.users.return_value.threads.return_value
        api.list.return_value.execute.side_effect=[{"threads":[{"id":"a"}],"nextPageToken":"p2"},{"threads":[{"id":"b"}]}]
        api.get.return_value.execute.side_effect=[{"messages":[{"id":"a","payload":{}}]},RuntimeError("private provider failure")]
        threads,failures,capped=read_threads(service,14,150,lambda message:None)
        self.assertEqual(len(threads),1);self.assertEqual(failures,1);self.assertFalse(capped)
        self.assertEqual(api.list.call_args.kwargs["pageToken"],"p2")
    def test_cap_reported(self):
        from unittest.mock import MagicMock
        service=MagicMock();api=service.users.return_value.threads.return_value
        api.list.return_value.execute.return_value={"threads":[{"id":"a"}],"nextPageToken":"p2"}
        api.get.return_value.execute.return_value={"messages":[]}
        self.assertTrue(read_threads(service,14,1,lambda message:None)[2])

if __name__ == "__main__": unittest.main()
