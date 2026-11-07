"""Orchestrate pinned handoffs, image preparation, ingestion, QA and delivery."""
import argparse
from datetime import date
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from .assets import local_asset
from .ingest import ingest, scan
from .images import prepare as localize
from .manifest import digest, read_json, verify_ready
from .__main__ import publish
from .qa import run_qa
from .gates import run_template_regressions
from .delivery import load_config, prepare as prepare_email, deliver
from .gmail import Gmail


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.report-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'交稿包不允许符号链接：{path}')
        if path.is_file():
            result[str(path.relative_to(root))] = digest(path.read_bytes())
    return result


def github_snapshot(repo, commit, destination):
    """Fetch an exact GitHub commit; never execute repository content."""
    if not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('仓库须为 https://github.com/OWNER/REPO URL')
    if not re.fullmatch(r'[a-fA-F0-9]{40}', commit):
        raise ValueError('须指定完整 40 位 commit SHA，不能使用浮动分支')

    def git(*args):
        result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', *args],
                                capture_output=True, text=True, timeout=180,
                                env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
        if result.returncode:
            raise ValueError('GitHub 获取失败；请检查仓库权限和 commit SHA')
        return result.stdout.strip()

    if not destination.exists():
        stage = Path(tempfile.mkdtemp(prefix='.source-', dir=destination.parent))
        try:
            git('init', str(stage))
            git('-C', str(stage), 'fetch', '--depth=1', repo, commit)
            actual = git('-C', str(stage), 'rev-parse', 'FETCH_HEAD')
            if actual.lower() != commit.lower():
                raise ValueError('获取的 commit 与交稿 SHA 不一致')
            git('-C', str(stage), 'checkout', '--detach', actual)
            stage.rename(destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    if git('-C', str(destination), 'rev-parse', 'HEAD').lower() != commit.lower():
        raise ValueError('交稿缓存 commit 不一致')
    if git('-C', str(destination), 'status', '--porcelain', '--untracked-files=all'):
        raise ValueError('交稿缓存已修改')
    return destination


def source_identity(author, metadata):
    if 'image_localization' in read_json(metadata):
        raise ValueError('pipeline 接收原始交稿；请使用原始输入')
    _, refs = scan(author.read_text('utf-8-sig'))
    assets = {ref: digest(local_asset(author.parent, ref).read_bytes())
              for ref, _ in refs if not ref.startswith(('https:', 'http:'))}
    return digest(json.dumps({'author': digest(author.read_bytes()),
                              'metadata': digest(metadata.read_bytes()), 'assets': assets},
                             sort_keys=True).encode())


def archive(html, issue, qa_dir, destination, provenance):
    """Expose a complete QA-passed release atomically, without overwriting one."""
    if destination.exists():
        marker = read_json(destination / 'archive.json')
        files = inventory(destination)
        files.pop('archive.json', None)
        if (marker['files'] != files or marker['html_sha256'] != digest(html.read_bytes())
                or marker['input_sha256'] != read_json(issue / 'manifest.json')['input_sha256']):
            raise ValueError('归档已存在但内容不同或已损坏；请检查或递增版本')
        return destination / html.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.archive-', dir=destination.parent))
    try:
        shutil.copyfile(html, stage / html.name)
        shutil.copytree(issue, stage / 'issue')
        shutil.copytree(qa_dir, stage / 'qa')
        atomic_json(stage / 'archive.json', {
            'schema_version': 1, 'html_sha256': digest(html.read_bytes()),
            'input_sha256': read_json(issue / 'manifest.json')['input_sha256'],
            'provenance': provenance, 'files': inventory(stage)})
        stage.rename(destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return destination / html.name


def run_pipeline(author, metadata, workspace, baseline=None, *, github_repo=None, commit=None,
                 update_baseline=False, email_config=None, send=False, token=None,
                 browser_executable=None, email_database=None, visual_policy='daily'):
    """Persist stage failures; QA gates release and unknown sends never auto-resend."""
    workspace = Path(workspace).resolve()
    baseline = Path(baseline).resolve() if baseline is not None else None
    workspace.mkdir(parents=True, exist_ok=True)
    with (workspace / '.pipeline.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        report = {'schema_version': 1, 'status': 'RUNNING', 'stage': 'source', 'steps': []}
        report.update(visual_policy=visual_policy, human_visual_review='not_performed')
        report_path = workspace / 'pipeline-report.json'

        def step(name, action):
            report['stage'] = name
            atomic_json(report_path, report)
            result = action()
            report['steps'].append({'stage': name, 'status': 'passed'})
            atomic_json(report_path, report)
            return result

        try:
            if visual_policy not in {'daily', 'same-issue'}:
                raise ValueError('visual_policy 须为 daily 或 same-issue')
            if visual_policy == 'daily' and (baseline is not None or update_baseline):
                raise ValueError('每日模式不使用本期基线；同一期比较请指定 --visual-policy same-issue')
            if visual_policy == 'same-issue' and baseline is None:
                raise ValueError('同一期比较须提供 --baseline-dir')
            if send and (email_config is None or token is None):
                raise ValueError('--send 须提供邮件配置和 token')
            if github_repo:
                if commit is None:
                    raise ValueError('GitHub 交稿缺少 commit')
                sources = workspace / 'sources'
                sources.mkdir(exist_ok=True)
                source = step('source', lambda: github_snapshot(
                    github_repo, commit, sources / digest((github_repo + commit).encode())))
                paths = []
                for ref in (author, metadata):
                    if Path(ref).is_absolute() or '..' in Path(ref).parts:
                        raise ValueError('GitHub 稿件路径须为仓库内相对路径')
                    path = (source / ref).resolve()
                    if not path.is_relative_to(source):
                        raise ValueError('交稿路径越出仓库')
                    paths.append(path)
                author, metadata = paths
                provenance = {'repo': github_repo, 'commit': commit.lower(),
                              'author': str(author.relative_to(source)),
                              'metadata': str(metadata.relative_to(source))}
            else:
                if commit is not None:
                    raise ValueError('--commit 须与 --github-repo 一起使用')
                author, metadata = Path(author).resolve(), Path(metadata).resolve()
                provenance = {'author': str(author), 'metadata': str(metadata)}
            info = read_json(metadata)
            day = date.fromisoformat(info['date']).isoformat()
            version = info['version']
            if not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', version):
                raise ValueError('版本须为 1–64 位字母、数字、点、下划线或横线')
            identity = source_identity(author, metadata)
            run = workspace / 'runs' / identity
            run.mkdir(parents=True, exist_ok=True)
            report.update(date=day, version=version, source_sha256=identity, provenance=provenance)
            bundle, receipt = run / 'prepared', run / 'prepared.json'

            def prepare_bundle():
                if bundle.exists():
                    if read_json(receipt) != inventory(bundle):
                        raise ValueError('本地化缓存校验失败；请检查缓存，不会自动覆盖')
                else:
                    localize(author, metadata, bundle)
                    atomic_json(receipt, inventory(bundle))
                return bundle

            step('images', prepare_bundle)
            issue = step('ingest', lambda: ingest(bundle / 'draft.md', bundle / 'issue.json', workspace / 'store').resolve())
            verify_ready(issue)
            report['issue'] = str(issue)
            html = step('publish', lambda: publish(issue, run / 'html', 1600, 12*1024*1024))

            def regressions():
                output = run / 'qa' / 'template-regressions'
                result = run_template_regressions(output, browser_executable=browser_executable)
                report['template_regression_report'] = str(output / 'report.json')
                if result['status'] != 'passed':
                    raise ValueError('固定样例回归失败；未归档、未准备邮件、未发送')
                return result

            step('template-regression', regressions)

            def quality():
                qa = run_qa(issue, html, run / 'qa', baseline,
                            update_baseline=update_baseline, browser_executable=browser_executable,
                            visual_mode='rules' if visual_policy == 'daily' else 'compare')
                report['qa_report'] = str(run / 'qa' / 'report.json')
                if qa['status'] != 'passed':
                    raise ValueError('QA 未通过；未归档、未准备邮件、未发送')
                return qa

            step('qa', quality)
            release = step('archive', lambda: archive(
                html, issue, run / 'qa', workspace / 'archive' / day / version, provenance))
            report.update(html=str(release), html_sha256=digest(release.read_bytes()))
            if email_config is not None:
                def email():
                    key, sha, raw = prepare_email(release, day, version, load_config(email_config))
                    if send:
                        return deliver(key, sha, raw, Path(email_database) if email_database else workspace.parent / 'email.sqlite3',
                                       Gmail.from_file(token), allow_resend=False)
                    preview = run / 'preview.eml'
                    preview.write_bytes(raw)
                    return {'status': 'PREVIEW', 'file': str(preview), 'sha256': sha}
                result = step('email', email)
                report['email'] = result
                report['status'] = ('COMPLETED' if result['status'] == 'SENT_VERIFIED' else
                                    'PREVIEW' if result['status'] == 'PREVIEW' else 'DELIVERY_PENDING')
            else:
                report['status'] = 'ARCHIVED'
            report['stage'] = 'complete'
        except Exception as error:
            report['status'] = 'FAILED'
            report['error'] = str(error)
            report['steps'].append({'stage': report['stage'], 'status': 'failed', 'detail': str(error)})
        atomic_json(report_path, report)
        if 'source_sha256' in report:
            atomic_json(workspace / 'runs' / report['source_sha256'] / 'pipeline-report.json', report)
        return report


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('author', type=Path)
    cli.add_argument('--metadata', type=Path, required=True)
    cli.add_argument('--workspace', type=Path, default=Path('var/pipeline'))
    cli.add_argument('--baseline-dir', type=Path)
    cli.add_argument('--visual-policy', choices=('daily', 'same-issue'), default='daily',
                     help='每日规则检查＋固定样例回归（默认），或追加同一期基线比较')
    cli.add_argument('--github-repo')
    cli.add_argument('--commit')
    cli.add_argument('--update-baseline', action='store_true')
    cli.add_argument('--email-config', type=Path)
    cli.add_argument('--send', action='store_true', help='实际发送；须已获具体收件人和内容授权')
    cli.add_argument('--token', type=Path)
    cli.add_argument('--email-database', type=Path, default=Path('var/email.sqlite3'))
    cli.add_argument('--browser-executable', type=Path)
    args = cli.parse_args()
    report = run_pipeline(args.author, args.metadata, args.workspace, args.baseline_dir,
                          github_repo=args.github_repo, commit=args.commit,
                          update_baseline=args.update_baseline, email_config=args.email_config,
                          send=args.send, token=args.token, browser_executable=args.browser_executable,
                          email_database=args.email_database, visual_policy=args.visual_policy)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['status'] in {'COMPLETED', 'PREVIEW', 'ARCHIVED'} else 1)


if __name__ == '__main__':
    main()
