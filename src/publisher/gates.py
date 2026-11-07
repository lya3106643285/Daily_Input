"""Fixed, approved template regressions required before any pipeline delivery."""
import json
from pathlib import Path

from .__main__ import publish
from .manifest import digest, read_json, verify_ready
from .qa import run_qa

ROOT = Path(__file__).resolve().parents[2]
REFERENCES = (('nine', '2026-11-06'), ('long', '2026-11-07'))


def run_template_regressions(output, *, browser_executable=None):
    output = Path(output).resolve()
    for name, _ in REFERENCES:
        protected = (ROOT / 'outputs' / 'stage4' / name).resolve()
        if output == protected or output in protected.parents or protected in output.parents:
            raise ValueError('回归输出不得覆盖或包含固定样例及其基线')
    output.mkdir(parents=True, exist_ok=True)
    report = {'status': 'failed', 'human_visual_review': 'not_performed', 'references': []}
    for name, day in REFERENCES:
        row = {'name': name, 'status': 'failed'}
        report['references'].append(row)
        try:
            source = ROOT / 'outputs' / 'stage4' / name
            issue = (source / 'issues' / day).resolve()
            baseline = source / 'baseline'
            # Never bootstrap or silently migrate these reviewed references.
            metadata = read_json(baseline / 'baseline.json')
            if not all(metadata.get(key) for key in ('identity', 'screenshot_sha256', 'environment')):
                raise ValueError('固定样例基线缺少稿件身份、截图哈希或字体环境记录')
            verify_ready(issue)
            if digest((issue / 'manifest.json').read_bytes()) != metadata['identity']['manifest_sha256']:
                raise ValueError('固定样例输入与已确认基线不一致')
            html = publish(issue, output / name / 'html', 1600, 12*1024*1024)
            qa_output = output / name / 'qa'
            qa = run_qa(issue, html, qa_output, baseline, browser_executable=browser_executable)
            row.update(status=qa['status'], issue=str(issue), baseline=str(baseline),
                       html_sha256=digest(html.read_bytes()), qa_report=str(qa_output / 'report.json'))
            if qa['status'] != 'passed':
                row['detail'] = '固定样例技术或截图回归失败'
        except Exception as error:
            row['detail'] = str(error)
    if len(report['references']) == 2 and all(row['status'] == 'passed' for row in report['references']):
        report['status'] = 'passed'
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    return report
