"""Verify or install the exact, licensed fonts used by publication QA.

No downloads or sudo are performed. Installation requires explicitly supplied
font directories and validates every locked file before copying anything.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / 'config/fonts.lock.json'


def load_lock():
    return json.loads(LOCK.read_text('utf-8'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_fonts():
    """Reject absent fonts and same-name replacements before taking screenshots."""
    if not shutil.which('fc-match'):
        raise ValueError('字体环境需要 fontconfig (fc-match)；请按视觉检查文档安装并验证锁定字体')
    lock = load_lock()
    failures = []
    for font in lock['files']:
        pattern = font['family'] + ':style=' + font['style']
        raw = subprocess.check_output(['fc-match', '-f', '%{file}', pattern], text=True)
        path = Path(raw)
        if not path.is_file() or digest(path) != font['sha256']:
            failures.append(font['filename'])
    if failures:
        raise ValueError('字体缺失或哈希不匹配：' + ', '.join(failures) +
                         '；请运行 python -m publisher.fonts verify，并按 config/fonts.lock.json 配置原版字体')
    return {
        'lock_sha256': digest(LOCK),
        'families': lock['versions'],
        'verified_files': len(lock['files']),
    }


def install_fonts(sources):
    """Install an already obtained and hash-verified font set in the user account."""
    lock = load_lock()
    available = {}
    for source in sources:
        if not source.is_dir():
            raise ValueError('字体源目录不存在：' + str(source))
        for path in source.rglob('*.ttf'):
            available.setdefault(path.name, []).append(path)
    selected = []
    for font in lock['files']:
        matches = [p for p in available.get(font['filename'], []) if digest(p) == font['sha256']]
        if not matches:
            raise ValueError('安装前验证失败，缺少锁定版本：' + font['filename'])
        selected.append(matches[0])
    destination = Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))/'fonts/daily-input'
    destination.mkdir(parents=True, exist_ok=True)
    for source in selected:
        target = destination/source.name
        if target.exists() and digest(target) != digest(source):
            raise ValueError('拒绝覆盖不同版本字体：' + str(target))
    for source in selected:
        target = destination/source.name
        with tempfile.NamedTemporaryFile(dir=destination, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(source.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    for name in ('MapleMono-OFL-1.1.txt', 'DejaVu-copyright.txt'):
        shutil.copyfile(ROOT/'config/font-licenses'/name, destination/name)
    subprocess.run(['fc-cache', '-f', str(destination)], check=True)
    return verify_fonts()


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('action', choices=('verify', 'install'))
    cli.add_argument('--source-dir', type=Path, action='append', default=[])
    args = cli.parse_args()
    if args.action == 'install' and not args.source_dir:
        cli.error('install requires --source-dir; fonts are not downloaded automatically')
    try:
        result = install_fonts(args.source_dir) if args.action == 'install' else verify_fonts()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        cli.exit(1, str(error)+'\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
