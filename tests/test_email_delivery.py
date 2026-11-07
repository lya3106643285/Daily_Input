"""Delivery contract tests with an in-memory Gmail, never a real send."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from publisher.delivery import deliver, load_config, main, parse, prepare, verify
from publisher.gmail import Gmail, GmailError, SendRejected


class FakeGmail:
    def __init__(self):
        self.sent = []
        self.messages = {}
        self.fail_send = None
        self.fail_get = False
        self.fail_find = False
        self.accept_before_failure = False

    def send(self, raw):
        self.sent.append(raw)
        key = str(len(self.sent))
        if not self.fail_send or self.accept_before_failure:
            self.messages[key] = {'id': key, 'raw': base64.urlsafe_b64encode(raw).decode(), 'labelIds': ['SENT']}
        if self.fail_send:
            raise self.fail_send
        return key

    def get(self, key):
        if self.fail_get:
            raise GmailError('读取失败')
        return self.messages[key]

    def find(self, message_id):
        if self.fail_find:
            raise GmailError('搜索失败')
        return [key for key, value in self.messages.items()
                if str(parse(base64.urlsafe_b64decode(value['raw']))['Message-ID']) == message_id]


class EmailDelivery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.html = self.root / 'issue.html'
        self.html.write_text('<html><body>中文日报</body></html>', 'utf-8')
        self.config_path = self.root / 'email.json'
        self.values = {'sender': 'author@example.com', 'to': ['reader@example.com'],
                       'cc': ['copy@example.com'], 'bcc': ['hidden@example.com']}
        self.config_path.write_text(json.dumps(self.values))
        self.config = load_config(self.config_path)
        self.key, self.sha, self.raw = prepare(self.html, '2026-11-07', '3.2', self.config)
        self.db = self.root / 'email.sqlite3'
        self.gmail = FakeGmail()

    def run_delivery(self):
        return deliver(self.key, self.sha, self.raw, self.db, self.gmail)

    def test_mime_and_exact_attachment(self):
        message = parse(self.raw)
        self.assertEqual(str(message['Bcc']), 'hidden@example.com')
        self.assertIn('2026-11-07', str(message['Subject']))
        attachment, = message.iter_attachments()
        self.assertEqual(attachment.get_content_type(), 'text/html')
        self.assertEqual(attachment.get_filename(), 'Daily_Input_2026-11-07_V3.2.html')
        self.assertEqual(attachment.get_payload(decode=True), self.html.read_bytes())

    def test_verified_send_and_rerun_skips(self):
        self.assertEqual(self.run_delivery()['status'], 'SENT_VERIFIED')
        self.assertTrue(self.run_delivery()['skipped'])
        self.assertEqual(len(self.gmail.sent), 1)

    def test_concurrent_runs_send_once(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.run_delivery(), range(4)))
        self.assertTrue(all(r['status'] == 'SENT_VERIFIED' for r in results))
        self.assertEqual(len(self.gmail.sent), 1)

    def test_accepted_unverified_never_resends(self):
        self.gmail.fail_get = True
        self.assertEqual(self.run_delivery()['status'], 'SEND_ACCEPTED')
        self.assertEqual(self.run_delivery()['status'], 'SEND_ACCEPTED')
        self.assertEqual(len(self.gmail.sent), 1)
        self.gmail.fail_get = False
        self.assertEqual(self.run_delivery()['status'], 'SENT_VERIFIED')

    def test_lost_response_reconciles_without_resend(self):
        self.gmail.fail_send = GmailError('网络超时')
        self.gmail.accept_before_failure = True
        self.assertEqual(self.run_delivery()['status'], 'SEND_UNKNOWN')
        self.assertEqual(self.run_delivery()['status'], 'SENT_VERIFIED')
        self.assertEqual(len(self.gmail.sent), 1)

    def test_unknown_once_resend_same_bytes_with_explanation(self):
        self.gmail.fail_send = GmailError('网络超时')
        self.run_delivery()
        self.gmail.fail_send = None
        self.assertEqual(self.run_delivery()['status'], 'SENT_VERIFIED')
        message = parse(self.gmail.sent[1])
        self.assertIn('网络超时', message.get_body(preferencelist=('plain',)).get_content())
        self.assertIn('核对两封邮件', message.get_body(preferencelist=('plain',)).get_content())
        self.assertEqual(next(message.iter_attachments()).get_payload(decode=True), self.html.read_bytes())
        self.assertNotEqual(parse(self.gmail.sent[0])['Message-ID'], message['Message-ID'])

    def test_unknown_has_at_most_two_attempts(self):
        self.gmail.fail_send = GmailError('网络超时')
        for _ in range(5):
            self.assertEqual(self.run_delivery()['status'], 'SEND_UNKNOWN')
        self.assertEqual(len(self.gmail.sent), 2)

    def test_failed_reconciliation_blocks_resend(self):
        self.gmail.fail_send = GmailError('网络超时')
        self.run_delivery()
        self.gmail.fail_find = True
        self.run_delivery()
        self.assertEqual(len(self.gmail.sent), 1)

    def test_crash_after_acceptance_is_recoverable(self):
        def crash(raw):
            self.gmail.sent.append(raw)
            raise KeyboardInterrupt()
        with patch.object(self.gmail, 'send', side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                self.run_delivery()
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT status FROM email_attempts').fetchone()[0], 'SEND_UNKNOWN')
        self.assertEqual(self.run_delivery()['status'], 'SENT_VERIFIED')
        self.assertEqual(len(self.gmail.sent), 2)

    def test_changed_attachment_requires_version_bump(self):
        self.run_delivery()
        self.html.write_text('<html><body>changed</body></html>')
        key, sha, raw = prepare(self.html, '2026-11-07', '3.2', self.config)
        with self.assertRaisesRegex(ValueError, '递增版本'):
            deliver(key, sha, raw, self.db, self.gmail)
        self.assertEqual(len(self.gmail.sent), 1)

    def test_verification_rejects_corruption_and_non_sent(self):
        remote = {'raw': base64.urlsafe_b64encode(self.raw).decode(), 'labelIds': []}
        self.assertFalse(verify(remote, self.raw))
        remote['labelIds'] = ['SENT']
        self.assertTrue(verify(remote, self.raw))
        message = parse(self.raw)
        next(message.iter_attachments()).set_payload('corrupted')
        remote['raw'] = base64.urlsafe_b64encode(message.as_bytes()).decode()
        self.assertFalse(verify(remote, self.raw))

    def test_verification_checks_bcc(self):
        message = parse(self.raw)
        del message['Bcc']
        remote = {'raw': base64.urlsafe_b64encode(message.as_bytes()).decode(), 'labelIds': ['SENT']}
        self.assertFalse(verify(remote, self.raw))

    def test_accepted_send_verifies_when_gmail_rewrites_message_id(self):
        self.gmail.fail_get = True
        self.assertEqual(self.run_delivery()['status'], 'SEND_ACCEPTED')
        message = parse(self.gmail.sent[0])
        message.replace_header('Message-ID', '<rewritten@mail.gmail.com>')
        self.gmail.messages['1']['raw'] = base64.urlsafe_b64encode(message.as_bytes()).decode()
        self.gmail.fail_get = False
        self.assertEqual(self.run_delivery()['status'], 'SENT_VERIFIED')
        self.assertEqual(len(self.gmail.sent), 1)

    def test_api_id_verification_still_rejects_wrong_identity_and_attachment(self):
        remote = {'id': 'known', 'raw': base64.urlsafe_b64encode(self.raw).decode(), 'labelIds': ['SENT']}
        self.assertFalse(verify(remote, self.raw, 'other'))
        self.assertTrue(verify(remote, self.raw, 'known'))
        message = parse(self.raw)
        next(message.iter_attachments()).set_payload('corrupted')
        remote['raw'] = base64.urlsafe_b64encode(message.as_bytes()).decode()
        self.assertFalse(verify(remote, self.raw, 'known'))

    def test_preview_does_not_load_oauth_or_create_database(self):
        output = self.root / 'preview.eml'
        with patch.object(sys, 'argv', ['delivery', str(self.html), '--date', '2026-11-07',
                                       '--version', '1', '--config', str(self.config_path),
                                       '--database', str(self.db), '--preview', str(output)]), \
                patch.object(Gmail, 'from_file', side_effect=AssertionError('OAuth used')), \
                patch('builtins.print'):
            main()
        self.assertTrue(output.is_file())
        self.assertFalse(self.db.exists())

    def test_config_deduplicates_and_supports_bcc_only(self):
        self.values.update(to=[], cc=[], bcc=['reader@example.com', 'READER@example.com'])
        self.config_path.write_text(json.dumps(self.values))
        config = load_config(self.config_path)
        self.assertEqual(config['bcc'], ['reader@example.com'])
        prepare(self.html, '2026-11-07', '1', config)

    def test_invalid_config_rejected(self):
        for change in ({'to': [] , 'cc': [], 'bcc': []}, {'to': ['x@example.com\r\nBcc: leak@example.com']},
                       {'to': 'reader@example.com'}, {'subject_prefix': 'a\nb'}, {'typo': True}):
            with self.subTest(change=change):
                self.config_path.write_text(json.dumps(self.values | change))
                with self.assertRaises(ValueError):
                    load_config(self.config_path)

    def test_invalid_attachment_and_version_rejected(self):
        for data in ('', '<html><body>truncated'):
            self.html.write_text(data)
            with self.assertRaises(ValueError):
                prepare(self.html, '2026-11-07', '1', self.config)
        with self.assertRaises(ValueError):
            prepare(self.html, '2026-11-07', '../bad', self.config)

    def test_transport_uses_raw_and_no_automatic_retry(self):
        with patch('publisher.gmail.request_json', return_value={'id': 'gmail-1'}) as request:
            self.assertEqual(Gmail('test-token').send(self.raw), 'gmail-1')
            args = request.call_args.args
            self.assertEqual(base64.urlsafe_b64decode(json.loads(args[1])['raw']), self.raw)
            self.assertEqual(request.call_count, 1)

    def test_transport_distinguishes_rejection_from_unknown(self):
        for status in (400, 401, 403, 429, 408, 500, 503):
            with self.subTest(status=status), patch('publisher.gmail.request_json',
                    side_effect=HTTPError('https://gmail.googleapis.com/', status, '', {}, None)) as request:
                kind = SendRejected if status < 500 and status != 408 else GmailError
                with self.assertRaises(kind) as error:
                    Gmail('test-token').send(self.raw)
                if status >= 500 or status == 408:
                    self.assertNotIsInstance(error.exception, SendRejected)
                self.assertEqual(request.call_count, 1)

    def test_rejection_recorded(self):
        self.gmail.fail_send = SendRejected('Gmail 拒绝发送：HTTP 403')
        self.assertEqual(self.run_delivery()['status'], 'SEND_REJECTED')
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT error FROM email_attempts').fetchone()[0],
                             'Gmail 拒绝发送：HTTP 403')


if __name__ == '__main__':
    unittest.main()
