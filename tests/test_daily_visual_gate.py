"""Daily rule gates, immutable reference regressions, and delivery blocking."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_stage3_acceptance as acceptance
import test_pipeline as pipeline_tests
from publisher.__main__ import publish
from publisher.ingest import ingest
from publisher.manifest import digest
from publisher.qa import run_qa, VIEWPORTS
from publisher.gates import run_template_regressions


class DailyRules(unittest.TestCase):
    def setUp(self):
        self.f = acceptance.Stage3()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.issue = ingest(self.f.source, self.f.metadata, self.f.store).resolve()
        self.html = publish(self.issue, self.f.output, 1600, 12*1024*1024)

    def test_daily_saves_screenshots_without_reading_comparing_or_creating_baseline(self):
        output = self.f.base / 'daily-qa'
        def browser(html, target, executable):
            for name in VIEWPORTS:
                (target / (name + '.png')).write_bytes(b'diagnostic-screenshot')
            return {name: {'screenshot': name + '.png',
                           'viewport': {'width': width, 'height': height}}
                    for name, (width, height) in VIEWPORTS.items()}
        with patch('publisher.qa.browser_check', side_effect=browser), patch('publisher.qa.visual_check') as compare:
            result = run_qa(self.issue, self.html, output, visual_mode='rules')
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['human_visual_review'], 'not_performed')
        visual, = [c for c in result['checks'] if c['id'] == 'visual']
        self.assertEqual(visual['status'], 'not_applicable')
        compare.assert_not_called()
        self.assertEqual(len(list(output.glob('*.png'))), 3)
        self.assertFalse(list(output.rglob('baseline.json')))

    def test_browser_rule_failure_cannot_pass_daily_gate(self):
        with patch('publisher.qa.browser_check', side_effect=ValueError('horizontal overflow')):
            result = run_qa(self.issue, self.html, self.f.base / 'qa', visual_mode='rules')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(next(c for c in result['checks'] if c['id'] == 'browser')['status'], 'failed')

    def test_rules_output_cannot_overwrite_an_existing_baseline(self):
        output = self.f.base / 'protected-baseline'
        output.mkdir()
        marker = output / 'baseline.json'
        marker.write_text('{"protected":true}')
        with self.assertRaises(ValueError), patch('publisher.qa.browser_check') as browser:
            run_qa(self.issue, self.html, output, visual_mode='rules')
        browser.assert_not_called()
        self.assertEqual(marker.read_text(), '{"protected":true}')
        self.assertEqual(len(list(output.iterdir())), 1)

    def test_daily_mode_rejects_baseline_or_update_and_preserves_same_issue_mode(self):
        output, baseline = self.f.base / 'qa', self.f.base / 'baseline'
        for kwargs in ({'baseline': baseline}, {'update_baseline': True}):
            with self.assertRaises(ValueError):
                run_qa(self.issue, self.html, output, visual_mode='rules', **kwargs)
        self.assertFalse(output.exists())
        with patch('publisher.qa.browser_check', return_value={}), patch('publisher.qa.visual_check', return_value={'mode': 'compared'}) as compare:
            result = run_qa(self.issue, self.html, output, baseline)
        self.assertEqual(result['status'], 'passed')
        compare.assert_called_once()

    def references(self):
        root = self.f.base / 'reference-repo'
        for name in ('nine', 'long'):
            source = root / 'outputs' / 'stage4' / name
            (source / 'issues').mkdir(parents=True)
            (source / 'issues' / acceptance.DAY).symlink_to(self.issue)
            baseline = source / 'baseline'
            baseline.mkdir()
            (baseline / 'baseline.json').write_text(json.dumps({
                'identity': {'manifest_sha256': digest((self.issue / 'manifest.json').read_bytes())},
                'environment': {'locked': True}, 'screenshot_sha256': {'dummy': 'hash'}}))
        return root

    def test_both_fixed_references_are_compared_and_failure_is_aggregated(self):
        root = self.references()
        with patch('publisher.gates.ROOT', root), patch('publisher.gates.REFERENCES', (('nine', acceptance.DAY), ('long', acceptance.DAY))), patch('publisher.gates.run_qa', side_effect=[{'status': 'passed'}, {'status': 'failed'}]) as qa:
            result = run_template_regressions(self.f.base / 'regressions')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual([r['status'] for r in result['references']], ['passed', 'failed'])
        self.assertEqual(qa.call_count, 2)
        for call in qa.call_args_list:
            self.assertNotIn('update_baseline', call.kwargs)
            self.assertEqual(call.args[3].name, 'baseline')

    def test_missing_reference_or_changed_manifest_blocks_regressions(self):
        root = self.references()
        for name in ('nine', 'long'):
            (root / 'outputs' / 'stage4' / name / 'baseline' / 'baseline.json').write_text('{}')
        with patch('publisher.gates.ROOT', root), patch('publisher.gates.REFERENCES', (('nine', acceptance.DAY), ('long', acceptance.DAY))), patch('publisher.gates.run_qa') as qa:
            result = run_template_regressions(self.f.base / 'regressions')
        self.assertEqual(result['status'], 'failed')
        qa.assert_not_called()

    def test_regression_output_cannot_overlap_approved_reference_files(self):
        root = self.references()
        baseline = root / 'outputs' / 'stage4' / 'nine' / 'baseline'
        before = (baseline / 'baseline.json').read_bytes()
        with patch('publisher.gates.ROOT', root), self.assertRaises(ValueError):
            run_template_regressions(baseline)
        self.assertEqual((baseline / 'baseline.json').read_bytes(), before)
        self.assertEqual(len(list(baseline.iterdir())), 1)


class PipelineDailyGate(unittest.TestCase):
    setUp = pipeline_tests.Pipeline.setUp
    run_pipeline = pipeline_tests.Pipeline.run_pipeline

    def test_fixed_regression_failure_blocks_preview_and_gmail(self):
        self.regressions.return_value = {'status': 'failed'}
        with patch('publisher.pipeline.Gmail.from_file') as gmail:
            result = self.run_pipeline(email_config=self.email_config, send=True, token='fake')
        self.assertEqual((result['status'], result['stage']), ('FAILED', 'template-regression'))
        self.assertFalse((self.workspace / 'archive').exists())
        self.assertFalse(list(self.workspace.rglob('preview.eml')))
        gmail.assert_not_called()

    def test_default_daily_does_not_compare_daily_content_to_previous_issue(self):
        result = self.run_pipeline()
        self.assertEqual(result['status'], 'ARCHIVED', result)
        self.assertEqual(result['visual_policy'], 'daily')
        self.assertEqual(result['human_visual_review'], 'not_performed')
        self.visual.assert_not_called()
        self.regressions.assert_called_once()

    def test_same_issue_mode_still_gates_on_visual_comparison(self):
        self.visual.side_effect = ValueError('same issue differs')
        result = self.run_pipeline(baseline=self.baseline, visual_policy='same-issue')
        self.assertEqual((result['status'], result['stage']), ('FAILED', 'qa'))
        self.assertFalse((self.workspace / 'archive').exists())

    def test_daily_rejects_silent_baseline_update(self):
        result = self.run_pipeline(update_baseline=True)
        self.assertEqual(result['status'], 'FAILED')
        self.assertFalse(self.baseline.exists())


if __name__ == '__main__':
    unittest.main()
