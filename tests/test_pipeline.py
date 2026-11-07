"""Pipeline integration checks; only browser and external networks are simulated."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_stage3_acceptance as acceptance
from test_image_localization import URL, RIGHTS, png, receipt
from test_email_delivery import FakeGmail
from publisher.pipeline import run_pipeline, github_snapshot
from publisher.gmail import GmailError
from publisher.delivery import parse, prepare as prepare_email, load_config, deliver


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.f = acceptance.Stage3()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.workspace = self.f.base / 'pipeline'
        self.baseline = self.f.base / 'baseline'
        self.email_config = self.f.base / 'email.json'
        self.email_config.write_text(json.dumps({'sender': 'author@example.com',
                                                 'to': ['reader@example.com']}))
        # Exercise real static QA and report creation. Browser coverage lives in Stage4.
        self.browser = patch('publisher.qa.browser_check', return_value={}).start()
        self.visual = patch('publisher.qa.visual_check', return_value={'mode': 'compared'}).start()
        self.regressions = patch('publisher.pipeline.run_template_regressions', return_value={'status': 'passed'}).start()
        self.addCleanup(patch.stopall)

    def run_pipeline(self, **kwargs):
        baseline = kwargs.pop('baseline', None)
        return run_pipeline(self.f.source, self.f.metadata, self.workspace,
                            baseline, **kwargs)

    def test_preview_attachment_exactly_matches_immutable_archive(self):
        with patch('publisher.pipeline.Gmail.from_file') as network:
            result = self.run_pipeline(email_config=self.email_config)
        self.assertEqual(result['status'], 'PREVIEW', result)
        network.assert_not_called()
        attachment, = parse(Path(result['email']['file']).read_bytes()).iter_attachments()
        self.assertEqual(attachment.get_payload(decode=True), Path(result['html']).read_bytes())
        release = Path(result['html']).parent
        self.assertTrue((release / 'issue' / 'original.md').is_file())
        self.assertEqual(json.loads((release / 'qa' / 'report.json').read_text())['status'], 'passed')
        self.assertEqual(json.loads((self.workspace / 'pipeline-report.json').read_text())['status'], 'PREVIEW')

    def test_remote_images_cached_and_same_revision_reused(self):
        self.f.add_body(self.f.material(URL))
        self.f.info['remote_images'] = {URL: RIGHTS}
        self.f.write()
        with patch('publisher.images.download', return_value=(png(), receipt())) as download:
            one, two = self.run_pipeline(), self.run_pipeline()
        self.assertEqual(one['status'], 'ARCHIVED', one)
        self.assertEqual(two['status'], 'ARCHIVED', two)
        self.assertEqual(one['issue'], two['issue'])
        self.assertEqual(one['html_sha256'], two['html_sha256'])
        download.assert_called_once()

    def test_failed_qa_blocks_release_and_all_email_activity_then_recovers(self):
        self.browser.side_effect = RuntimeError('Chromium unavailable')
        with patch('publisher.pipeline.Gmail.from_file') as gmail:
            result = self.run_pipeline(email_config=self.email_config, send=True, token='fake-token')
        self.assertEqual((result['status'], result['stage']), ('FAILED', 'qa'))
        self.assertFalse((self.workspace / 'archive').exists())
        gmail.assert_not_called()
        self.browser.side_effect = None
        self.assertEqual(self.run_pipeline()['status'], 'ARCHIVED')

    def test_missing_rights_fails_before_download_and_ingest(self):
        self.f.add_body(self.f.material(URL))
        self.f.write()
        with patch('publisher.images.download') as network:
            result = self.run_pipeline()
        self.assertEqual((result['status'], result['stage']), ('FAILED', 'images'))
        network.assert_not_called()
        self.assertFalse((self.workspace / 'store').exists())

    def test_changed_input_same_version_preserves_archive(self):
        first = self.run_pipeline()
        original = Path(first['html']).read_bytes()
        self.f.text += '\n正文新增。\n'
        self.f.write()
        result = self.run_pipeline()
        self.assertEqual((result['status'], result['stage']), ('FAILED', 'ingest'))
        self.assertEqual(Path(first['html']).read_bytes(), original)

    def test_cache_and_archive_tampering_are_rejected(self):
        result = self.run_pipeline()
        archive = Path(result['html'])
        archive.write_bytes(archive.read_bytes() + b'tampered')
        self.assertEqual(self.run_pipeline()['stage'], 'archive')
        bundle = self.workspace / 'runs' / result['source_sha256'] / 'prepared'
        (bundle / 'draft.md').write_text('tampered')
        self.assertEqual(self.run_pipeline()['stage'], 'images')

    def test_verified_send_rerun_and_concurrency_send_once(self):
        gmail = FakeGmail()
        with patch('publisher.pipeline.Gmail.from_file', return_value=gmail):
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(lambda _: self.run_pipeline(
                    email_config=self.email_config, send=True, token='fake'), range(3)))
        self.assertTrue(all(r['status'] == 'COMPLETED' for r in results), results)
        self.assertEqual(len(gmail.sent), 1)

    def test_unknown_send_never_automatically_resends(self):
        gmail = FakeGmail()
        gmail.fail_send = GmailError('timeout')
        with patch('publisher.pipeline.Gmail.from_file', return_value=gmail):
            one = self.run_pipeline(email_config=self.email_config, send=True, token='fake')
            gmail.fail_send = None
            two = self.run_pipeline(email_config=self.email_config, send=True, token='fake')
        self.assertEqual(one['status'], 'DELIVERY_PENDING')
        self.assertEqual(two['email']['status'], 'SEND_UNKNOWN')
        self.assertEqual(len(gmail.sent), 1)

    def test_unknown_send_reconciles_accepted_message(self):
        gmail = FakeGmail()
        gmail.fail_send = GmailError('response lost')
        gmail.accept_before_failure = True
        with patch('publisher.pipeline.Gmail.from_file', return_value=gmail):
            one = self.run_pipeline(email_config=self.email_config, send=True, token='fake')
            two = self.run_pipeline(email_config=self.email_config, send=True, token='fake')
        self.assertEqual(one['status'], 'DELIVERY_PENDING')
        self.assertEqual(two['status'], 'COMPLETED')
        self.assertEqual(len(gmail.sent), 1)

    def test_existing_mail_ledger_is_shared_with_pipeline(self):
        initial = self.run_pipeline()
        gmail = FakeGmail()
        key, sha, raw = prepare_email(Path(initial['html']), acceptance.DAY, '1', load_config(self.email_config))
        database = self.f.base / 'existing-email.sqlite3'
        deliver(key, sha, raw, database, gmail)
        with patch('publisher.pipeline.Gmail.from_file', return_value=gmail):
            result = self.run_pipeline(email_config=self.email_config, send=True, token='fake',
                                       email_database=database)
        self.assertEqual(result['status'], 'COMPLETED', result)
        self.assertEqual(len(gmail.sent), 1)

    def test_publish_failure_resumes_without_another_image_download(self):
        self.f.add_body(self.f.material(URL))
        self.f.info['remote_images'] = {URL: RIGHTS}
        self.f.write()
        with patch('publisher.images.download', return_value=(png(), receipt())) as download:
            with patch('publisher.pipeline.publish', side_effect=OSError('disk unavailable')):
                failed = self.run_pipeline()
            succeeded = self.run_pipeline()
        self.assertEqual((failed['status'], failed['stage']), ('FAILED', 'publish'))
        self.assertEqual(succeeded['status'], 'ARCHIVED', succeeded)
        download.assert_called_once()

    def test_version_traversal_and_send_without_config_rejected(self):
        self.f.info['version'] = '../escape'
        self.f.write()
        self.assertEqual(self.run_pipeline()['status'], 'FAILED')
        self.assertEqual(self.run_pipeline(send=True)['status'], 'FAILED')

    def test_github_input_uses_snapshot_and_records_commit(self):
        commit = 'a' * 40
        with patch('publisher.pipeline.github_snapshot', return_value=self.f.author) as checkout:
            result = run_pipeline(Path('draft.md'), Path('issue.json'), self.workspace,
                                  self.baseline, visual_policy='same-issue', github_repo='https://github.com/example/daily', commit=commit)
        self.assertEqual(result['status'], 'ARCHIVED', result)
        self.assertEqual(result['provenance']['commit'], commit)
        checkout.assert_called_once()
        with patch('publisher.pipeline.github_snapshot', return_value=self.f.author):
            result = run_pipeline(Path('../escape.md'), Path('issue.json'), self.workspace,
                                  self.baseline, visual_policy='same-issue', github_repo='https://github.com/example/daily', commit=commit)
        self.assertEqual(result['status'], 'FAILED')

    def test_github_requires_exact_commit_and_checks_checkout(self):
        root = self.f.base / 'snapshot'
        with patch('publisher.pipeline.subprocess.run') as git:
            with self.assertRaises(ValueError):
                github_snapshot('https://github.com/example/daily', 'master', root)
            git.assert_not_called()
        root.mkdir()
        commit = 'b' * 40
        with patch('publisher.pipeline.subprocess.run', side_effect=[
                SimpleNamespace(returncode=0, stdout=commit),
                SimpleNamespace(returncode=0, stdout=' M draft.md')]):
            with self.assertRaisesRegex(ValueError, '已修改'):
                github_snapshot('https://github.com/example/daily', commit, root)


if __name__ == '__main__':
    unittest.main()
