"""Ingest author Markdown without modifying it; publish immutable issue revisions."""
import argparse
from contextlib import contextmanager
from datetime import date
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from .assets import image_uri, local_asset
from .content import MD, MARKER, raw, parse
from .manifest import ID, PREFIX, digest, read_json, validate

SECTIONS = ('AI 前沿', '硬件、图形技术与价格', '科技与游戏事件', '论文精读',
            'GitHub 经典项目', 'GitHub 特殊新项目', 'Gaming 技术解读', '游戏推荐', '每日好图')


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()


def write(path, data):
    with path.open('wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def issue_lock(store, day):
    locks = store / '.locks'
    locks.mkdir(exist_ok=True)
    with (locks / f'{day}.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def scan(text):
    """Only top-level parsed blocks are candidates for identity marks."""
    from markdown_it.tree import SyntaxTreeNode
    nodes = SyntaxTreeNode(MD.parse(text)).children
    pending = None
    section = article = None
    objects, images = [], []
    for node in nodes:
        line = node.map[0] + 1
        if node.type == 'html_block':
            match = MARKER.fullmatch(node.content.strip())
            if not match or pending:
                raise ValueError(f'第 {line} 行：未知、重复或悬空的 ID 标记')
            pending = (match[1], node.map[0])
            continue
        kind = None
        if node.type == 'heading':
            if node.tag == 'h2':
                kind = 'section'
            elif node.tag == 'h3':
                kind = 'article'
            elif node.tag == 'h4':
                kind = 'sub'
        elif node.type in ('fence', 'table', 'blockquote'):
            kind = 'material'
        elif (node.type == 'paragraph' and len(node.children[0].children) == 1
              and node.children[0].children[0].type == 'image'):
            kind = 'material'
            from urllib.parse import unquote
            images.append((unquote(node.children[0].children[0].attrs['src']), line))
        if pending and not kind:
            raise ValueError(f'第 {line} 行：ID 未紧邻可登记对象')
        if kind:
            obj = {'kind':kind, 'name':raw(node), 'line':line, 'start':node.map[0],
                   'marker': pending[0] if pending else None,
                   'parent_item': None if kind == 'section' else section if kind == 'article' else article}
            if kind != 'section' and obj['parent_item'] is None:
                raise ValueError(f'第 {line} 行：{kind} 缺少所属栏目/文章')
            if kind == 'material':
                # Content fingerprint assists exact pairing; never fuzzy-match identities.
                obj['name'] = digest('\n'.join(text.splitlines()[node.map[0]:node.map[1]]).encode())
            objects.append(obj)
            if kind == 'section':
                section, article = obj, None
            elif kind == 'article':
                article = obj
        pending = None
    if pending:
        raise ValueError('原稿末尾存在悬空 ID 标记')
    return objects, images


def assign(items, metadata, previous):
    old = previous.get('objects', {})
    retired = dict(previous.get('retired', {}))
    counters = dict(previous.get('counters', {}))
    legacy = set(previous.get('legacy_ids', []))
    explicit = metadata.get('identity', {})
    initial_legacy = set(metadata.get('legacy_ids', []))
    if previous and initial_legacy - legacy:
        raise ValueError('旧 ID 只能在首次入库时导入')
    used, result, unresolved = set(), {}, []
    for key in set(old) | set(retired):
        for kind, prefix in PREFIX.items():
            match = re.fullmatch(rf'{prefix}-(\d+)', key)
            if match:
                counters[kind] = max(counters.get(kind, 0), int(match[1]))
    # Reserve explicit IDs before attempting name matching, allowing duplicate names with marks.
    for item in items:
        line_key = str(item['line'])
        key = item['marker']
        if line_key in explicit:
            if key and explicit[line_key] != key:
                raise ValueError(f'第 {line_key} 行：identity 与原标记不一致')
            key = explicit[line_key]
        item['explicit_new'] = line_key in explicit and explicit[line_key] is None
        if key:
            if not ID.fullmatch(key) or key in used or key in retired:
                raise ValueError(f'第 {line_key} 行：未知、重复或停用 ID：{key}')
            if previous and (key not in old or old[key]['kind'] != item['kind']):
                raise ValueError(f'第 {line_key} 行：未知 ID 或对象类型错误：{key}')
            if not previous:
                if key not in initial_legacy and line_key not in explicit:
                    raise ValueError(f'第 {line_key} 行：首次导入标记须列入 legacy_ids 或 identity：{key}')
                legacy.add(key)
            item['id'] = key
            used.add(key)
    for item in items:
        if 'id' in item:
            continue
        candidates = [key for key, obj in old.items() if key not in used
                      and obj['kind'] == item['kind'] and obj.get('name') == item['name']]
        # Sections and articles may move; global uniqueness is required.
        duplicates = [x for x in items if x['kind'] == item['kind'] and x['name'] == item['name']
                      and 'id' not in x and not x['explicit_new']]
        if not item['explicit_new'] and len(candidates) == 1 and len(duplicates) == 1:
            item['id'] = candidates[0]
            used.add(item['id'])
        elif not item['explicit_new'] and candidates:
            raise ValueError(f'第 {item["line"]} 行：身份无法唯一配对：{item["name"]}；候选 {candidates}')
        else:
            unresolved.append(item)
    for item in unresolved:
        leftovers = [key for key,obj in old.items() if key not in used and obj['kind'] == item['kind']]
        if leftovers and not item['explicit_new']:
            raise ValueError(f'第 {item["line"]} 行：标记丢失或改题无法配对：{item["name"]}；待配对 {leftovers}；用 identity 指定 ID 或 null 新建')
        kind = item['kind']
        counters[kind] = counters.get(kind, 0) + 1
        key = f'{PREFIX[kind]}-{counters[kind]:03}'
        while key in used or key in retired:
            counters[kind] += 1
            key = f'{PREFIX[kind]}-{counters[kind]:03}'
        item['id'] = key
        used.add(key)
    sections = []
    for item in items:
        key, kind = item['id'], item['kind']
        obj = {'kind':kind, 'name':item['name'], 'line':item['line']}
        if item['parent_item']:
            obj['parent'] = item['parent_item']['id']
        if kind == 'section':
            canonical = old.get(key, {}).get('canonical', item['name'])
            if canonical not in SECTIONS or canonical in sections:
                raise ValueError(f'第 {item["line"]} 行：未知或重复栏目：{canonical}')
            sections.append(canonical)
            try:
                info = metadata['sections'][canonical]
                for field in ('topic', 'digest'):
                    if not isinstance(info[field], str) or not info[field].strip():
                        raise ValueError(f'栏目 {canonical} 缺少 {field}')
            except (KeyError, TypeError) as exc:
                raise ValueError(f'栏目 {canonical} 缺少 topic/digest 输入') from exc
            obj.update(canonical=canonical, edition='A' if SECTIONS.index(canonical) < 3 else 'B',
                       topic=info['topic'], digest=info['digest'])
        result[key] = obj
    if set(sections) != set(SECTIONS):
        raise ValueError(f'必须提交完整九栏，缺少：{sorted(set(SECTIONS)-set(sections))}')
    for key, obj in old.items():
        if key not in used and obj['kind'] != 'excerpt':
            retired[key] = obj
    return result, retired, counters, legacy


def build_work(text, items):
    lines = text.splitlines(keepends=True)
    for item in reversed(items):
        if not item['marker']:
            lines.insert(item['start'], f'<!-- id: {item["id"]} -->\n\n')
    return ''.join(lines)


def ingest(author, metadata_path, store, *, fault=None):
    """Commit a complete revision. fault(name) may inject recovery-test failures."""
    author, metadata_path, store = Path(author).resolve(), Path(metadata_path).resolve(), Path(store).resolve()
    metadata = read_json(metadata_path)
    day = date.fromisoformat(metadata['date']).isoformat()
    if day != metadata['date'] or not isinstance(metadata.get('version'), str) or not metadata['version'].strip():
        raise ValueError('date 须为 ISO 日期；version 须为非空字符串')
    raw_bytes = author.read_bytes()
    text = raw_bytes.decode('utf-8-sig')
    if re.search(r'【[^】]*】|待补', text) or re.search(r'【[^】]*】|待补', json.dumps(metadata, ensure_ascii=False)):
        raise ValueError(f'期次 {day}：存在占位文字或待补来源')
    items, refs = scan(text)
    paths = {}
    for ref, line in refs:
        try:
            path = local_asset(author.parent, ref)
            image_uri(path, 1600)  # Fully decode and validate before any visible commit.
            paths[ref] = path.read_bytes()
        except (OSError, ValueError) as exc:
            raise ValueError(f'期次 {day} 第 {line} 行 图片 {ref}：{exc}') from exc
    fingerprint = digest(json_bytes({'author':digest(raw_bytes), 'metadata':metadata,
                                    'assets':{k:digest(v) for k,v in paths.items()}}))
    store.mkdir(parents=True, exist_ok=True)
    with issue_lock(store, day):
        target = store / day
        previous = {}
        if target.exists():
            if not target.is_symlink():
                raise ValueError(f'入库目标不是受管理的修订链接：{target}')
            from .manifest import verify_ready
            verify_ready(target.resolve())
            previous = validate(read_json(target / 'manifest.json'))
            if previous.get('input_sha256') == fingerprint:
                return target
            if previous['version'] == metadata['version']:
                raise ValueError(f'期次 {day}：同版本输入不同，请使用新 version 提交修订')
        revisions = store / '.revisions' / day
        revisions.mkdir(parents=True, exist_ok=True)
        # Only reachable commits form history; an interrupted pre-switch directory is an orphan.
        history = list(previous.get('history', []))
        if previous:
            history.append({'version':previous['version'], 'input_sha256':previous['input_sha256']})
        if any(record['version'] == metadata['version'] for record in history):
            raise ValueError(f'期次 {day}：version 已在历史中使用；请使用新 version')
        objects, retired, counters, legacy = assign(items, metadata, previous)
        manifest = {'schema_version':1, 'date':day, 'version':metadata['version'],
                    'lang':metadata.get('lang','zh-CN'), 'note':metadata.get('note',''),
                    'objects':objects, 'retired':retired, 'counters':counters,
                    'legacy_ids':sorted(legacy), 'assets':list(paths), 'input_sha256':fingerprint,
                    'history':history, 'links':metadata.get('links', {}),
                    'excerpts':{}, 'excerpt_history':dict(previous.get('excerpt_history', {})),
                    'asset_records':{}}
        # Record a material's explicit local-source keys in encounter order; suffixes never recycle.
        from markdown_it.tree import SyntaxTreeNode
        by_line = {i['line']:i for i in items}
        current = None
        used_excerpts = set()
        for node in SyntaxTreeNode(MD.parse(text)).children:
            item = by_line.get(node.map[0]+1)
            if item:
                current = item if item['kind'] == 'material' else None
            if node.type != 'paragraph' or not raw(node).startswith('材料出处：'):
                if node.type not in ('html_block',) and not item and not (node.type == 'paragraph' and raw(node).startswith(('图注：','表注：','代码说明：'))):
                    current = None
                continue
            if current:
                for key in re.findall(r'\]\(excerpt:([A-Za-z0-9_-]+)\)', raw(node)):
                    try:
                        entry = metadata['excerpts'][key]
                        if not all(isinstance(entry[f],str) and entry[f].strip() for f in ('title','text')):
                            raise ValueError('摘录篇名和正文不能为空')
                    except (KeyError, TypeError) as exc:
                        raise ValueError(f'第 {node.map[0]+1} 行：缺少本地摘录 {key}') from exc
                    material = current['id']
                    history = manifest['excerpt_history'].setdefault(material, {})
                    # Reappearing deleted excerpt keys receive fresh suffixes.
                    previous_active = previous.get('excerpts', {}).get(material, {})
                    if key in previous_active:
                        sid = previous_active[key]['id']
                    else:
                        index = max(history.values(), default=0) + 1
                        history[f'{key}@{metadata["version"]}'] = index
                        sid = f'src-{material}' + (f'-{index:03}' if index > 1 else '')
                    active = manifest['excerpts'].setdefault(material, {})
                    if key in active:
                        continue
                    active[key] = dict(entry, id=sid)
                    objects[sid] = {'kind':'excerpt','parent':material,'name':key}
                    used_excerpts.add(sid)
                if current['line'] in [line for _,line in refs]:
                    ref = next(ref for ref,line in refs if line == current['line'])
                    record = manifest['asset_records'].setdefault(ref, {'sha256':digest(paths[ref]), 'sources':[]})
                    record['sources'].append(raw(node)[len('材料出处：'):].strip())
        for key, obj in previous.get('objects', {}).items():
            if obj['kind'] == 'excerpt' and key not in used_excerpts:
                retired[key] = obj
        work = build_work(text, items)
        validate(manifest)
        from .__main__ import template_environment, Anchors
        env = template_environment()
        title, groups = parse(work, manifest, env, lambda ref: 'data:image/png;base64,AA==')
        page = env.get_template('daily.html.j2').render(title=title, date=day, version=manifest['version'],
                    groups=groups, lang=manifest['lang'], note=manifest['note'])
        anchors = Anchors()
        anchors.feed(page)
        if anchors.refs - anchors.ids:
            raise ValueError(f'期次 {day}：悬空页内链接 {sorted(anchors.refs-anchors.ids)}')
        stage = Path(tempfile.mkdtemp(prefix='.staging-', dir=revisions))
        committed = None
        link = store / f'.{day}-{uuid.uuid4().hex}.link'
        try:
            write(stage / 'original.md', raw_bytes)
            write(stage / 'issue.json', metadata_path.read_bytes())
            write(stage / f'Daily_Input_{day}.md', work.encode())
            for ref, data in paths.items():
                asset = stage / ref
                asset.parent.mkdir(parents=True, exist_ok=True)
                write(asset, data)
            write(stage / 'manifest.json', json_bytes(manifest))
            if fault:
                fault('before_ready')
            files = {str(p.relative_to(stage)):digest(p.read_bytes()) for p in stage.rglob('*') if p.is_file()}
            write(stage / 'READY', json_bytes({'files':files}))
            for folder in sorted((p for p in stage.rglob('*') if p.is_dir()), reverse=True):
                sync_dir(folder)
            sync_dir(stage)
            if fault:
                fault('before_commit')
            committed = revisions / fingerprint
            if committed.exists():
                # A hard kill after rename leaves a complete but unreachable candidate.
                shutil.rmtree(committed)
            stage.rename(committed)
            sync_dir(revisions)
            if fault:
                fault('after_revision')
            link.symlink_to(committed.relative_to(store))
            link.replace(target)
            sync_dir(store)
        except BaseException:
            # Orphan complete revisions are not published. Remove on handled failures;
            # a hard-kill orphan is reused/recovered on retry below.
            if committed and committed.exists() and (not target.exists() or target.resolve() != committed):
                shutil.rmtree(committed)
            raise
        finally:
            link.unlink(missing_ok=True)
            if stage.exists():
                shutil.rmtree(stage)
        return target


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('author', type=Path)
    cli.add_argument('--metadata', type=Path, required=True)
    cli.add_argument('--store', type=Path, required=True)
    cli.add_argument('--output-dir', type=Path)
    args = cli.parse_args()
    try:
        issue = ingest(args.author, args.metadata, args.store)
        if args.output_dir:
            from .__main__ import publish
            print(publish(issue, args.output_dir, 1600, 12*1024*1024))
        else:
            print(issue)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        cli.exit(1, f'{args.author} 入库失败：{exc}\n')


if __name__ == '__main__':
    main()
