"""Font identity and visual environment mismatch must fail closed."""
from pathlib import Path
import hashlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from publisher import fonts
from publisher.qa import visual_check, capture_screenshot, run_qa
from unittest.mock import Mock


class FontEnvironment(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.font = self.root/'locked.ttf'
        self.font.write_bytes(b'font fixture')
        self.lock = self.root/'fonts.lock.json'
        self.lock.write_text(json.dumps({
            'versions': {'Fixture': '1'},
            'files': [{'filename':self.font.name, 'family':'Fixture','style':'Regular',
                       'sha256':hashlib.sha256(self.font.read_bytes()).hexdigest()}]
        }))
        self.addCleanup(patch.stopall)
        patch.object(fonts, 'LOCK', self.lock).start()

    def test_same_name_replaced_font_is_rejected(self):
        with patch.object(fonts.shutil, 'which', return_value='/usr/bin/fc-match'), patch.object(
                fonts.subprocess, 'check_output', return_value=str(self.font)):
            self.assertEqual(fonts.verify_fonts()['verified_files'],1)
            self.font.write_bytes(b'different font with same name')
            with self.assertRaisesRegex(ValueError, '哈希不匹配'):
                fonts.verify_fonts()

    def test_missing_fontconfig_is_rejected(self):
        with patch.object(fonts.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'fontconfig'):
                fonts.verify_fonts()

    def test_install_validates_entire_set_before_writing(self):
        destination=self.root/'user-data'
        with patch.dict(os.environ, XDG_DATA_HOME=str(destination)):
            with self.assertRaisesRegex(ValueError, '缺少锁定版本'):
                fonts.install_fonts([self.make_empty()])
        self.assertFalse(destination.exists())

    def make_empty(self):
        folder=self.root/'empty'
        folder.mkdir()
        return folder

    def test_capture_retries_only_transient_error_and_has_a_limit(self):
        from playwright.sync_api import Error
        page = Mock()
        page.screenshot.side_effect = [Error('Unable to capture screenshot'), None]
        self.assertEqual(capture_screenshot(page, self.root/'out.png'), 2)
        self.assertEqual(page.evaluate.call_count, 1)
        page.reset_mock()
        page.screenshot.side_effect = Error('Unable to capture screenshot')
        with self.assertRaises(Error):
            capture_screenshot(page, self.root/'out.png')
        self.assertEqual(page.screenshot.call_count, 3)
        page.reset_mock()
        page.screenshot.side_effect = Error('Permission denied')
        with self.assertRaises(Error):
            capture_screenshot(page, self.root/'out.png')
        self.assertEqual(page.screenshot.call_count, 1)

    def test_overlapping_output_cannot_overwrite_reference_files(self):
        baseline=self.root/'baseline'
        baseline.mkdir()
        reference=baseline/'narrow.png'
        reference.write_bytes(b'approved reference')
        for output in (baseline, baseline/'child', self.root):
            with self.assertRaisesRegex(ValueError,'不得相同或相互包含'):
                run_qa(self.root/'issue', self.root/'issue.html', output, baseline)
            self.assertEqual(reference.read_bytes(),b'approved reference')
        self.assertFalse((baseline/'report.json').exists())

    def test_other_issue_or_changed_html_cannot_reuse_bound_baseline(self):
        baseline=self.root/'baseline'
        baseline.mkdir()
        metadata=baseline/'baseline.json'
        metadata.write_text(json.dumps({'identity':{'date':'2026-11-04','html_sha256':'approved'}}))
        original=metadata.read_bytes()
        for identity in ({'date':'2026-11-05','html_sha256':'approved'},
                         {'date':'2026-11-04','html_sha256':'changed'}):
            with self.assertRaisesRegex(ValueError,'基线不属于当前稿件'):
                visual_check(self.root/'output',baseline,False,identity=identity)
        self.assertEqual(metadata.read_bytes(),original)

    def test_baseline_png_replacement_is_detected_before_comparison(self):
        baseline=self.root/'baseline'
        baseline.mkdir()
        metadata=baseline/'baseline.json'
        metadata.write_text(json.dumps({'screenshot_sha256':{'narrow':'expected-hash'}}))
        (baseline/'narrow.png').write_bytes(b'replaced')
        with self.assertRaisesRegex(ValueError,'登记哈希不一致'):
            visual_check(self.root/'output',baseline,False)

    def test_environment_mismatch_does_not_change_baseline(self):
        baseline=self.root/'baseline'
        baseline.mkdir()
        metadata=baseline/'baseline.json'
        metadata.write_text(json.dumps({'environment':{'fonts':'approved'}}))
        original=metadata.read_bytes()
        with self.assertRaisesRegex(ValueError, '视觉环境与基线不同'):
            visual_check(self.root/'output',baseline,False,{'fonts':'different'})
        self.assertEqual(metadata.read_bytes(),original)


if __name__=='__main__':
    unittest.main()
