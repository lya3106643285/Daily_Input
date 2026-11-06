"""Core phase-four black-box QA, including a real visual baseline comparison.

Browser tests require an installed Playwright Chromium and permission to launch it.
"""
from pathlib import Path
import json
import os
import re
import subprocess
import sys
import unittest

import test_stage3_acceptance as stage3

ROOT = Path(__file__).resolve().parents[1]


class Stage4(unittest.TestCase):
    def setUp(self):
        self.fixture = stage3.Stage3()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.image()
        self.fixture.add_body(self.fixture.material())
        self.fixture.add_body('#### 中英文与公式\n\n技术说明 test α，公式 E = mc²。\n\n'
                              '| 名称 | 数值 |\n| --- | ---: |\n| 测试 | 1 |\n\n'
                              '表注：自制测试数据。\n\n材料出处：测试作者自制2026-11-06。\n\n'
                              '```python\nprint("offline")\n```\n\n材料出处：测试作者自制2026-11-06。')
        # Long reading flow, while preserving the same nine-section author protocol.
        self.fixture.add_body('\n\n'.join('长文段落' + str(i) + '：这是独立测试构造的连续阅读内容。' * 8
                                          for i in range(35)))
        self.fixture.ingest()
        self.html = self.fixture.output / f'{stage3.STEM}.html'
        self.original = self.html.read_text('utf-8')
        self.baseline = self.fixture.base / 'baseline'
        self.sequence = 0

    def run_qa(self, *extra, expected=None):
        self.sequence += 1
        output = self.fixture.base / f'qa-{self.sequence}'
        env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))
        command = [sys.executable, '-m', 'publisher.qa', str(self.fixture.store / stage3.DAY),
                   '--html', str(self.html), '--output-dir', str(output),
                   '--baseline-dir', str(self.baseline)]
        executable = os.environ.get('STAGE4_BROWSER_EXECUTABLE')
        if executable:
            command.extend(['--browser-executable', executable])
        command.extend(map(str, extra))
        process = subprocess.run(command, cwd=ROOT, env=env, text=True,
                                 capture_output=True, timeout=180)
        self.assertTrue((output / 'report.json').is_file(), process.stderr + process.stdout)
        self.assertTrue((output / 'report.md').is_file(), process.stderr + process.stdout)
        report = json.loads((output / 'report.json').read_text('utf-8'))
        self.assertEqual(report['status'], 'passed' if process.returncode == 0 else 'failed', report)
        if expected is not None:
            self.assertEqual(report['status'], expected, json.dumps(report, ensure_ascii=False))
        return report

    @staticmethod
    def checks(report):
        return {entry['id']: entry for entry in report['checks']}

    def test_21_real_browser_explicit_baseline_compare_and_visible_change(self):
        # Missing baseline must be reported; a normal check cannot silently approve it.
        missing = self.run_qa(expected='failed')
        self.assertEqual(self.checks(missing)['visual']['status'], 'failed')
        self.assertFalse(any(self.baseline.rglob('*.png')))
        created = self.run_qa('--update-baseline', expected='passed')
        self.assertEqual(set(self.checks(created)),
                         {'content', 'structure', 'media', 'links', 'html', 'package', 'browser', 'visual'})
        self.assertEqual(self.checks(created)['browser']['status'], 'passed')
        self.assertEqual(len(list(self.baseline.rglob('*.png'))), 3)
        compared = self.run_qa(expected='passed')
        self.assertEqual(self.checks(compared)['visual']['status'], 'passed')
        self.html.write_text(self.original.replace('</head>',
            '<style>body { background: #00ff00 !important; }</style></head>'), encoding='utf-8')
        changed = self.run_qa(expected='failed')
        self.assertEqual(self.checks(changed)['visual']['status'], 'failed')
        self.assertEqual(self.checks(changed)['browser']['status'], 'passed')
        # Comparison must leave the approved baseline intact.
        self.html.write_text(self.original, encoding='utf-8')
        self.run_qa(expected='passed')

    def test_22_common_broken_outputs_report_failure_in_relevant_check(self):
        broken = {
            'content': self.original.replace('正文标识0，这是测试构造内容。', '', 1),
            'structure': self.original.replace('id="art-002"', 'id="art-001"', 1),
            'media': re.sub(r'(<img\b[^>]*src=")[^"]+', r'\g<1>data:image/png;base64,bm90LWltYWdl', self.original, count=1),
            'links': self.original.replace('</article>', '<a href="not-a-public-source.txt">错误来源</a></article>', 1),
            'html': self.original[:self.original.index('</article>')],
            'package': self.original.replace('</head>', '<link rel="stylesheet" href="https://example.org/missing.css"></head>', 1),
        }
        for kind, text in broken.items():
            with self.subTest(check=kind):
                self.assertNotEqual(text, self.original, 'fixture mutation must actually change the HTML')
                self.html.write_text(text, encoding='utf-8')
                report = self.run_qa(expected='failed')
                self.assertEqual(self.checks(report)[kind]['status'], 'failed', report)
        self.html.write_text(self.original, encoding='utf-8')
        size = self.run_qa('--max-bytes', '1', expected='failed')
        self.assertEqual(self.checks(size)['package']['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
