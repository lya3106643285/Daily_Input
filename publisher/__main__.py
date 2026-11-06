"""Publish one complete issue: python -m publisher ISSUE_FOLDER --output-dir ARCHIVE."""
import argparse
from datetime import date
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import tempfile

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from PIL import Image

from .assets import image_uri, local_asset
from .content import parse


class Anchors(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.refs = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            key = attrs['id']
            if not key or key in self.ids:
                raise ValueError(f'重复或空锚点：{key}')
            self.ids.add(key)
        if tag == 'a' and attrs.get('href', '').startswith('#'):
            self.refs.add(attrs['href'][1:])


def unique_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f'清单字段重复：{key}')
        obj[key] = value
    return obj


def publish(root, output, max_edge, max_bytes):
    root = root.resolve()
    if not (root / 'READY').is_file():
        raise ValueError('本期缺少 READY 交接标记')
    manifest = json.loads((root / 'manifest.json').read_text('utf-8'), object_pairs_hook=unique_keys)
    day = date.fromisoformat(manifest['date']).isoformat()
    stem = f'Daily_Input_{day}'
    assets = manifest['assets']
    if not isinstance(assets, list) or not all(isinstance(k, str) for k in assets) or len(set(assets)) != len(assets):
        raise ValueError('assets 应为不重复的图片相对路径列表')
    paths = {key: local_asset(root, key) for key in assets}
    used = {}

    def picture(ref):
        if ref not in paths:
            raise ValueError(f'图片未登记：{ref}')
        if ref not in used:
            used[ref] = image_uri(paths[ref], max_edge)
        return used[ref]

    env = Environment(loader=FileSystemLoader(Path(__file__).resolve().parents[1] / 'templates'),
                      autoescape=True, undefined=StrictUndefined)
    text = (root / f'{stem}.md').read_text('utf-8-sig')
    title, groups = parse(text, manifest, env, picture)
    if used.keys() != paths.keys():
        raise ValueError(f'图片清单含未使用的图片：{sorted(paths.keys()-used.keys())}')
    page = env.get_template('daily.html.j2').render(
        title=title, date=day, version=manifest['version'], groups=groups,
        lang=manifest.get('lang', 'zh-CN'), note=manifest.get('note', ''))
    check = Anchors()
    check.feed(page)
    if check.refs - check.ids:
        raise ValueError(f'悬空的页内链接：{sorted(check.refs-check.ids)}')
    data = page.encode('utf-8')
    if len(data) > max_bytes:
        raise ValueError(f'成品 {len(data)} 字节，超过本期上限 {max_bytes}')
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f'{stem}.html'
    if destination.exists() and destination.read_bytes() == data:
        return destination
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=output, delete=False) as stream:
            temp = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(destination)
    finally:
        if temp:
            temp.unlink(missing_ok=True)
    return destination


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('issue', type=Path)
    cli.add_argument('--output-dir', type=Path, required=True)
    cli.add_argument('--max-edge', type=int, default=1600)
    cli.add_argument('--max-bytes', type=int, default=12*1024*1024)
    args = cli.parse_args()
    if min(args.max_edge, args.max_bytes) <= 0:
        cli.error('长边和字节上限须大于零')
    try:
        print(publish(args.issue, args.output_dir, args.max_edge, args.max_bytes))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        cli.exit(1, f'{args.issue} 出版失败：{exc}\n')
