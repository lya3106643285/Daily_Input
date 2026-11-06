"""Shared manifest and immutable-issue validation."""
from datetime import date
import hashlib
import json
from pathlib import Path
import re

ID = re.compile(r'[A-Za-z][A-Za-z0-9_-]*\Z')
PREFIX = {'section': 'sec', 'article': 'art', 'sub': 'sub', 'material': 'mat'}


def unique_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f'清单字段重复：{key}')
        obj[key] = value
    return obj


def read_json(path):
    return json.loads(Path(path).read_text('utf-8-sig'), object_pairs_hook=unique_keys)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate(manifest):
    if not isinstance(manifest, dict):
        raise ValueError('清单应为 JSON 对象')
    date.fromisoformat(manifest['date'])
    if not isinstance(manifest['version'], str) or not manifest['version'].strip():
        raise ValueError('version 须为非空字符串')
    objects = manifest['objects']
    if not isinstance(objects, dict):
        raise ValueError('objects 须为对象映射')
    legacy = manifest.get('legacy_ids', [])
    for key, obj in objects.items():
        if not ID.fullmatch(key) or not isinstance(obj, dict):
            raise ValueError(f'无效对象：{key}')
        kind = obj.get('kind')
        if kind not in {*PREFIX, 'excerpt'}:
            raise ValueError(f'未知对象类型：{key}')
        if manifest.get('schema_version') == 1 and key not in legacy:
            pattern = (rf'{PREFIX[kind]}-[0-9]{{3,}}' if kind != 'excerpt'
                       else r'src-[A-Za-z][A-Za-z0-9_-]*')
            if not re.fullmatch(pattern, key):
                raise ValueError(f'编号前缀不符：{key}')
        parent = obj.get('parent')
        expected = {'article':'section', 'sub':'article', 'material':'article', 'excerpt':'material'}
        if kind == 'section':
            if parent is not None or obj.get('edition') not in ('A', 'B'):
                raise ValueError(f'栏目归属无效：{key}')
        elif parent not in objects or objects[parent].get('kind') != expected[kind]:
            raise ValueError(f'对象父项不存在或类型错误：{key}')
    retired = manifest.get('retired', {})
    if set(retired) & set(objects):
        raise ValueError('停用编号不可重新激活')
    assets = manifest['assets']
    if (not isinstance(assets, list) or not all(isinstance(k, str) for k in assets)
            or len(set(assets)) != len(assets)):
        raise ValueError('assets 应为不重复的图片相对路径列表')
    return manifest


def verify_ready(root):
    ready = root / 'READY'
    if not ready.is_file():
        raise ValueError('本期缺少 READY 交接标记')
    # Empty READY remains valid for pre-ingestion, manually prepared legacy issues.
    if ready.read_bytes().strip():
        record = read_json(ready)
        for ref, sha in record['files'].items():
            path = (root / ref).resolve()
            if not path.is_relative_to(root) or not path.is_file() or digest(path.read_bytes()) != sha:
                raise ValueError(f'READY 内容校验失败：{ref}')
        required = {'manifest.json', f'Daily_Input_{read_json(root / "manifest.json")["date"]}.md'}
        if not required <= record['files'].keys():
            raise ValueError('READY 未绑定完整清单与工作副本')
