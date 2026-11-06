"""Independent black-box checks for the phase-three publication contract.

Run: .venv/bin/python -m unittest discover -s tests -v
Fixtures are deliberately fabricated and identify their provenance accordingly.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DAY = '2026-11-06'
STEM = f'Daily_Input_{DAY}'
SECTIONS = ['AI 前沿', '硬件、图形技术与价格', '科技与游戏事件', '论文精读',
            'GitHub 经典项目', 'GitHub 特殊新项目', 'Gaming 技术解读', '游戏推荐', '每日好图']


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids, self.refs, self.images, self.external_resources = [], [], [], []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'a' and attrs.get('href', '').startswith('#'):
            self.refs.append(attrs['href'][1:])
        if tag == 'img':
            self.images.append(attrs)
        if tag in {'img', 'script', 'iframe', 'link'}:
            resource = attrs.get('src', attrs.get('href', ''))
            if resource and not resource.startswith(('data:', '#')):
                self.external_resources.append(resource)


class Stage3(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='daily-acceptance-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.author = self.base / 'author'
        self.author.mkdir()
        (self.author / 'assets').mkdir()
        self.source = self.author / 'draft.md'
        self.metadata = self.author / 'issue.json'
        self.store = self.base / 'store'
        self.output = self.base / 'html'
        self.info = {'date': DAY, 'version': '1', 'sections': {
            name: {'topic': f'主题{i}', 'digest': f'提要{i}'} for i, name in enumerate(SECTIONS)}}
        self.text = '# 每日信息输入\n\n' + '\n\n'.join(
            '## ' + name + '\n\n栏目导语' + str(i) + '\n\n' + self.article(i)
            for i, name in enumerate(SECTIONS)) + '\n'
        self.text = self.text.replace('## 硬件、', self.article(99) + '\n\n## 硬件、', 1)
        self.write()

    @staticmethod
    def article(number, body=None):
        return (f'### 文章{number}\n\n体裁：讲解综述\n\n来源说明：测试作者 · 测试记录2026-11-06第{number}节（构造用例）。\n\n'
                + (body or f'正文标识{number}，这是测试构造内容。'))

    def write(self):
        self.source.write_text(self.text, encoding='utf-8')
        self.metadata.write_text(json.dumps(self.info, ensure_ascii=False), encoding='utf-8')

    def command(self, *args):
        return subprocess.run([sys.executable, '-m', *map(str, args)], cwd=ROOT, text=True,
                              capture_output=True, timeout=45)

    def ingest(self, ok=True, *extra):
        self.write()
        p = self.command('publisher.ingest', self.source, '--metadata', self.metadata,
                         '--store', self.store, '--output-dir', self.output, *extra)
        if ok:
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        else:
            self.assertNotEqual(p.returncode, 0, p.stderr + p.stdout)
        return p

    def manifest(self):
        return json.loads((self.store / DAY / 'manifest.json').read_text('utf-8'))

    def work(self):
        return (self.store / DAY / f'{STEM}.md').read_text('utf-8')

    def html(self):
        return (self.output / f'{STEM}.html').read_text('utf-8')

    def image(self, name='tiny.png', fmt='PNG', size=(60, 20), transparent=False, orientation=None):
        path = self.author / 'assets' / name
        mode = 'RGBA' if transparent else 'RGB'
        im = Image.new(mode, size, (255, 0, 0, 64) if transparent else (255, 0, 0))
        opts = {}
        if orientation:
            exif = Image.Exif()
            exif[274] = orientation
            opts['exif'] = exif
        im.save(path, format=fmt, **opts)
        return path

    def material(self, reference='assets/tiny.png', credit='测试作者自制，CC0，测试记录2026-11-06第1节。'):
        return f'![测试图片替代文字]({reference})\n\n图注：测试图片说明。\n\n材料出处：{credit}'

    def add_body(self, body):
        self.text = self.text.replace('正文标识0，这是测试构造内容。',
                                      '正文标识0，这是测试构造内容。\n\n' + body, 1)

    def revision(self):
        self.text = self.work()
        self.info['version'] = str(int(self.info['version']) + 1)

    def test_01_nine_sections_ten_articles_author_bytes_offline_and_repeat(self):
        original = self.source.read_bytes()
        self.ingest()
        self.assertEqual(self.source.read_bytes(), original)
        self.assertTrue((self.store / DAY / 'READY').is_file())
        m, html = self.manifest(), self.html()
        self.assertEqual(sum(o['kind'] == 'section' for o in m['objects'].values()), 9)
        self.assertEqual(sum(o['kind'] == 'article' for o in m['objects'].values()), 10)
        page = Page(html)
        self.assertEqual(len(page.ids), len(set(page.ids)))
        self.assertTrue(set(page.refs) <= set(page.ids))
        self.assertFalse(page.external_resources)
        self.assertNotIn('<!-- id:', html)
        before = (self.store / DAY).resolve()
        self.ingest()
        self.assertEqual((self.store / DAY).resolve(), before)
        self.assertEqual(self.html(), html)

    def test_02_same_version_changed_content_rejected_preserves_previous(self):
        self.ingest()
        old, html = (self.store / DAY).resolve(), self.html()
        self.text += '\n新增正文。\n'
        self.ingest(False)
        self.assertEqual((self.store / DAY).resolve(), old)
        self.assertEqual(self.html(), html)

    def test_03_marked_rename_move_and_new_id_after_deleted(self):
        self.ingest()
        old = self.manifest()['objects']
        key = next(k for k, o in old.items() if o.get('name') == '文章0')
        self.revision()
        pattern = rf'<!-- id: {key} -->\s*### 文章0.*?(?=<!-- id: art-|<!-- id: sec-|\Z)'
        match = re.search(pattern, self.text, re.S)
        self.assertIsNotNone(match)
        block = match[0].replace('### 文章0', '### 改题并移动')
        self.text = self.text[:match.start()] + self.text[match.end():]
        self.text += '\n\n' + block
        self.ingest()
        new = self.manifest()['objects']
        self.assertEqual(new[key]['name'], '改题并移动')
        self.assertNotEqual(new[key]['parent'], old[key]['parent'])
        self.assertIn(f'id="{key}"', self.html())

    def test_04_unmarked_duplicate_titles_cannot_guess_identity(self):
        self.text = self.text.replace('### 文章99', '### 文章0')
        self.ingest()
        self.info['version'] = '2'
        self.ingest(False)

    def test_05_bad_structure_sources_placeholders(self):
        cases = {
            'duplicate-section': ('## GitHub 经典项目', '## AI 前沿'),
            'h5': ('正文标识0，', '##### 错误层级\n\n正文标识0，'),
            'source': ('来源说明：测试作者 · 测试记录2026-11-06第0节（构造用例）。\n\n', ''),
            'placeholder': ('正文标识0', '【未填正文】'),
            'pending': ('测试记录2026-11-06第0节', '待补'),
        }
        original = self.text
        for name, (before, after) in cases.items():
            with self.subTest(name=name):
                self.text = original.replace(before, after, 1)
                p = self.ingest(False)
                self.assertIn(DAY, p.stderr)
                self.assertFalse((self.store / DAY / 'READY').exists())

    def test_06_materials_table_code_quote_and_code_marker_not_parsed(self):
        self.add_body('| 项目 | 数值 |\n| --- | ---: |\n| a\\|b | 1 |\n\n表注：构造数据。\n\n材料出处：测试作者自制2026-11-06。\n\n'
                      '```python\n# 不是刊名\n## 不是栏目\n<!-- id: fake -->\nprint("<>&")\n```\n\n代码说明：构造代码。\n\n材料出处：测试作者自制2026-11-06。\n\n'
                      '> 引文第一段。\n>\n> 引文第二段。\n\n材料出处：测试作者《测试记录》2026-11-06，第2节。')
        self.ingest()
        html = self.html()
        for fragment in ('<table>', '<blockquote', '<pre><code>', 'a|b', '&lt;!-- id: fake --&gt;'):
            self.assertIn(fragment, html)
        self.assertNotIn('fake', self.manifest()['objects'])

    def test_07_bad_table_columns_and_unclosed_fence(self):
        original = self.text
        for body in ('| a | b |\n| --- | --- |\n| only-one |\n\n表注：测试。\n\n材料出处：自制测试2026-11-06。',
                     '```python\nx = 1\n\n材料出处：自制测试。'):
            with self.subTest(body=body):
                self.text = original
                self.add_body(body)
                self.ingest(False)

    def test_08_a_b_source_positions_same_genre(self):
        self.ingest()
        articles = re.findall(r'<article\b[^>]*>(.*?)</article>', self.html(), re.S)
        self.assertEqual(len(articles), 10)
        self.assertLess(articles[0].index('测试记录2026-11-06第0节'), articles[0].index('正文标识0'))
        self.assertGreater(articles[-1].index('测试记录2026-11-06第8节'), articles[-1].index('正文标识8'))

    def test_09_image_formats_alpha_orientation_repeat_and_source_preservation(self):
        files = [self.image(), self.image('photo.jpg', 'JPEG', (120, 60), orientation=6),
                 self.image('transparent.png', transparent=True), self.image('long.webp', 'WEBP', (20, 1800))]
        originals = {p: p.read_bytes() for p in files}
        for path in files + files[:1]:
            self.add_body(self.material('assets/' + path.name))
        self.ingest()
        images = Page(self.html()).images
        self.assertEqual(len(images), 5)
        for tag in images:
            self.assertTrue(tag['alt'])
            self.assertTrue(tag['src'].startswith('data:image/'))
            im = Image.open(BytesIO(base64.b64decode(tag['src'].split(',', 1)[1])))
            im.load()
            self.assertLessEqual(max(im.size), 1600)
        for p in files:
            self.assertEqual(p.read_bytes(), originals[p])
            self.assertEqual((self.store / DAY / 'assets' / p.name).read_bytes(), originals[p])
        decoded = [Image.open(BytesIO(base64.b64decode(tag['src'].split(',', 1)[1]))) for tag in images]
        self.assertIn((60, 120), [im.size for im in decoded])
        self.assertTrue(any(im.mode == 'RGBA' and im.getextrema()[-1][0] == 64 for im in decoded))

    def test_10_missing_corrupt_and_escape_images(self):
        original = self.text
        (self.author / 'assets' / 'corrupt.png').write_bytes(b'not an image')
        outside = self.base / 'outside.png'
        Image.new('RGB', (5, 5)).save(outside)
        (self.author / 'assets' / 'escaped.png').symlink_to(outside)
        for path in ('assets/missing.png', 'assets/corrupt.png', '../outside.png', 'assets/escaped.png'):
            with self.subTest(path=path):
                self.text = original
                self.add_body(self.material(path))
                self.ingest(False)

    def test_11_output_size_failure_preserves_existing_html(self):
        self.ingest()
        original = self.html()
        p = self.command('publisher', self.store / DAY, '--output-dir', self.output, '--max-bytes', '1')
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.html(), original)

    def test_12_local_excerpt_round_trip_and_material_scoping(self):
        self.info['excerpts'] = {'record': {'title': '测试记录·2026-11-06第1节', 'text': '离线可读的原文摘录。'}}
        self.image()
        self.add_body(self.material(credit='测试作者，CC0，[本地原文](excerpt:record)。'))
        self.ingest()
        m, page = self.manifest(), Page(self.html())
        mats = [k for k, o in m['objects'].items() if o['kind'] == 'material']
        self.assertEqual(len(mats), 1)
        self.assertIn('src-' + mats[0], page.ids)
        self.assertIn(mats[0], page.refs)
        self.assertIn('离线可读的原文摘录。', self.html())

    def test_13_legacy_ids_import_and_unknown_marker_rejection(self):
        self.text = self.text.replace('### 文章0', '<!-- id: requests -->\n\n### 文章0', 1)
        self.info['legacy_ids'] = ['requests']
        self.ingest()
        self.assertIn('id="requests"', self.html())
        self.revision()
        self.text = self.text.replace('<!-- id: requests -->', '<!-- id: unknown -->', 1)
        self.ingest(False)

    def test_14_concurrent_identical_ingest_has_one_visible_revision(self):
        args = ('publisher.ingest', self.source, '--metadata', self.metadata, '--store', self.store)
        with ThreadPoolExecutor(max_workers=3) as pool:
            result = list(pool.map(lambda _: self.command(*args), range(3)))
        self.assertTrue(all(p.returncode == 0 for p in result), '\n'.join(p.stderr for p in result))
        self.assertTrue((self.store / DAY / 'READY').is_file())
        self.assertEqual(sum(o['kind'] == 'article' for o in self.manifest()['objects'].values()), 10)

    def test_15_dangling_anchor_and_duplicate_marker_rejected(self):
        self.add_body('[错误跳转](#does-not-exist)')
        self.ingest(False)
        self.text = self.text.replace('[错误跳转](#does-not-exist)', '普通文字')
        self.ingest()
        self.revision()
        self.text = self.text.replace('<!-- id: art-002 -->', '<!-- id: art-001 -->')
        self.ingest(False)

    def test_16_deleted_id_is_retired_and_never_reused(self):
        self.ingest()
        self.revision()
        deleted = next(k for k, o in self.manifest()['objects'].items() if o.get('name') == '文章99')
        self.text = re.sub(rf'<!-- id: {deleted} -->\s*### 文章99.*?(?=<!-- id: sec-|<!-- id: art-|\Z)', '', self.text, count=1, flags=re.S)
        self.ingest()
        self.assertIn(deleted, self.manifest()['retired'])
        self.assertNotIn(deleted, self.manifest()['objects'])
        self.revision()
        self.text += '\n\n' + self.article(100)
        self.ingest()
        added = next(k for k, o in self.manifest()['objects'].items() if o.get('name') == '文章100')
        self.assertGreater(int(added.split('-')[1]), int(deleted.split('-')[1]))
        self.assertIn(deleted, self.manifest()['retired'])

    def test_17_commit_interruption_keeps_same_visible_pair_then_retry(self):
        from publisher.ingest import ingest
        self.ingest()
        old, manifest = (self.store / DAY).resolve(), self.manifest()
        self.revision()
        self.text = self.text.replace('### 文章0', '### 改题保留身份', 1)
        self.write()
        for point in ('before_ready', 'before_commit', 'after_revision'):
            def fail(name, point=point):
                if name == point:
                    raise RuntimeError('simulated interruption at ' + point)
            with self.subTest(point=point):
                with self.assertRaises(RuntimeError):
                    ingest(self.source, self.metadata, self.store, fault=fail)
                self.assertEqual((self.store / DAY).resolve(), old)
                self.assertEqual(self.manifest(), manifest)
        self.ingest()
        self.assertNotEqual((self.store / DAY).resolve(), old)
        self.assertIn('改题保留身份', self.html())

    def test_19_imported_legacy_excerpt_id_and_return_are_preserved(self):
        self.image()
        self.add_body('<!-- id: material-1 -->\n\n' + self.material(credit='测试作者，CC0，[本地原文](excerpt:record)。'))
        self.info['excerpts'] = {'record': {'title': '旧记录第1节', 'text': '应离线保留的旧摘录。', 'id': 'excerpt-1'}}
        self.info['legacy_ids'] = ['material-1', 'excerpt-1']
        self.ingest()
        page = Page(self.html())
        self.assertIn('material-1', page.ids)
        self.assertIn('excerpt-1', page.ids)
        self.assertIn('excerpt-1', page.refs)
        self.assertIn('material-1', page.refs)
        self.assertEqual(self.manifest()['objects']['excerpt-1']['parent'], 'material-1')

    def test_20_old_material_link_maps_to_embedded_excerpt(self):
        self.image()
        self.add_body(self.material(credit='测试作者，CC0，[本地原文](old-record.md#part)。'))
        self.info['links'] = {'old-record.md#part': 'excerpt:old'}
        self.info['excerpts'] = {'old': {'title': '旧记录第1节', 'text': '映射链接对应的离线摘录。'}}
        self.ingest()
        page = Page(self.html())
        self.assertIn('映射链接对应的离线摘录。', self.html())
        self.assertIn('src-mat-001', page.ids)
        self.assertIn('src-mat-001', page.refs)
        self.assertIn('mat-001', page.refs)
        self.assertNotIn('href="old-record.md#part"', self.html())

    def test_18_original_prebuilt_issue_still_publishes(self):
        p = self.command('publisher', ROOT / 'docs/3 出版引擎/示例期', '--output-dir', self.output)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue((self.output / f'{STEM}.html').is_file())


if __name__ == '__main__':
    unittest.main()
