"""Read top-level Markdown blocks; fenced examples never create headings or IDs."""
import re
from urllib.parse import urlsplit, unquote

from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode
from markupsafe import Markup

MD = MarkdownIt('commonmark', {'html': True}).enable('table')
MARKER = re.compile(r'<!-- id: ([A-Za-z][A-Za-z0-9_-]*) -->')
FIELDS = ('体裁：', '来源说明：', '图注：', '表注：', '代码说明：', '材料出处：')


def inline(text):
    tokens = MD.parseInline(text)[0].children
    for t in tokens:
        if t.type in {'image', 'html_inline'}:
            raise ValueError('图片须独立成段，正文不能插入 HTML')
        if t.type == 'link_open':
            url = t.attrGet('href')
            u = urlsplit(url)
            if not (url.startswith('#') or (u.scheme in {'http', 'https'} and u.netloc)):
                raise ValueError(f'请使用公开资料链接或本期锚点：{url}')
    return Markup(MD.renderer.renderInline(tokens, MD.options, {}))


def raw(n):
    return n.children[0].content if n.type in ('heading', 'paragraph', 'th', 'td') else ''


class Blocks:
    def __init__(self, text):
        self.nodes = SyntaxTreeNode(MD.parse(text)).children
        self.pos = 0

    def peek(self):
        return self.nodes[self.pos] if self.pos < len(self.nodes) else None

    def take(self):
        node = self.peek()
        if node is None:
            raise ValueError('原稿提前结束')
        self.pos += 1
        return node

    def field(self, name, required=False):
        n = self.peek()
        if n and n.type == 'paragraph' and raw(n).startswith(name):
            value = raw(self.take())[len(name):].strip()
            if not value:
                raise ValueError(f'{name}缺少内容')
            return value
        if required:
            raise ValueError(f'缺少字段 {name}')
        return ''

    def fields(self, name):
        lines = []
        while value := self.field(name):
            lines.append(inline(value))
        if not lines:
            raise ValueError(f'缺少字段 {name}')
        return lines


def render_material(blocks, node, identifier, media, picture):
    caption = ''
    if node.type == 'paragraph':
        image = node.children[0].children[0]
        if not image.content.strip():
            raise ValueError('图片缺少替代文本')
        body = media.image(picture(unquote(image.attrs['src'])), image.content)
        caption = inline(blocks.field('图注：', required=True))
    elif node.type == 'fence':
        if not node.info.strip():
            raise ValueError('代码围栏缺少语言')
        body = media.code(node.info.strip(), node.content, inline(blocks.field('代码说明：')))
    elif node.type == 'blockquote':
        if any(n.type != 'paragraph' for n in node.children):
            raise ValueError('引文需要使用自然段')
        body = media.quote([inline(raw(n)) for n in node.children])
    else:
        rows = [n.children for n in node.walk() if n.type == 'tr']
        align = [c.attrs.get('style', 'text-align:left').split(':')[-1] for c in rows[0]]
        cells = [[inline(raw(c)) for c in row] for row in rows]
        body = media.table(cells[0], cells[1:], align)
        caption = inline(blocks.field('表注：', required=True))
    return media.figure(identifier, blocks.fields('材料出处：'), caption=caption, caller=lambda: body)


def parse(text, manifest, env, picture):
    blocks = Blocks(text)
    first = blocks.take()
    if first.type != 'heading' or first.tag != 'h1':
        raise ValueError('原稿应从一级刊名开始')
    title = raw(first)
    objects = manifest['objects']
    content = env.get_template('components/content.html.j2').module
    media = env.get_template('components/media.html.j2').module
    sections, seen = [], set()
    section = article = None
    opening = True

    def register(marker, kind, parent, name):
        if not marker:
            matches = [k for k,v in objects.items() if v.get('kind') == kind
                       and v.get('parent') == parent and v.get('name') == name]
            if len(matches) != 1:
                raise ValueError(f'请为 {name} 在清单与工作副本中配对 ID')
            marker = matches[0]
        obj = objects.get(marker, {})
        if (marker in seen or obj.get('kind') != kind or obj.get('parent') != parent
                or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', marker)):
            raise ValueError(f'ID 重复、类型或归属有误：{marker}')
        seen.add(marker)
        return marker, obj

    while (n := blocks.peek()) is not None:
        try:
            n = blocks.take()
            marker = None
            if n.type == 'html_block':
                match = MARKER.fullmatch(n.content.strip())
                if not match:
                    raise ValueError('未知 HTML 标记')
                marker = match[1]
                n = blocks.take()
            value = raw(n)
            if n.type == 'heading' and n.tag == 'h2':
                key, obj = register(marker, 'section', None, value)
                section = dict(id=key, name=value, topic=obj['topic'], digest=obj['digest'],
                               edition=obj['edition'], number=f'{len(sections)+1:02}', intro='', articles=[])
                sections.append(section)
                article = None
            elif n.type == 'heading' and n.tag == 'h3' and section:
                key, obj = register(marker, 'article', section['id'], value)
                genre = blocks.field('体裁：', required=True)
                if genre not in ('新闻摘要', '讲解综述'):
                    raise ValueError(f'未知体裁：{genre}')
                article = dict(id=key, title=value, sources=blocks.fields('来源说明：'),
                               lang=obj.get('lang', manifest.get('lang', 'zh-CN')), summary=obj.get('summary',''),
                               parts=[], paragraphs=0)
                section['articles'].append(article)
                opening = True
            elif article is None and section and n.type == 'paragraph' and not marker:
                section['intro'] = Markup(' ').join(filter(None, [section['intro'], inline(value)]))
            elif article is None:
                raise ValueError('内容缺少所属文章')
            elif n.type == 'heading' and n.tag == 'h4':
                key = register(marker, 'sub', article['id'], value)[0] if marker else None
                article['parts'].append(content.heading(value, key))
                opening = True
            elif (n.type in ('fence', 'blockquote', 'table') or
                  (n.type == 'paragraph' and len(n.children[0].children) == 1
                   and n.children[0].children[0].type == 'image')):
                key = register(marker, 'material', article['id'], '')[0] if marker else None
                article['parts'].append(render_material(blocks, n, key, media, picture))
            elif n.type == 'paragraph' and not marker and not value.startswith(FIELDS):
                article['parts'].append(content.paragraph(inline(value), opening))
                article['paragraphs'] += 1
                opening = False
            else:
                raise ValueError(f'字段、ID 或 Markdown 块位置有误：{n.type}')
        except (ValueError, KeyError, OSError) as error:
            raise ValueError(f'第 {n.map[0]+1} 行：{error}') from error
    if not sections or set(objects) != seen:
        raise ValueError('栏目为空或清单含未匹配的对象')
    groups = []
    for s in sections:
        edition = s['edition']
        if not s['articles'] or edition not in ('A','B'):
            raise ValueError(f'版别无效或栏目为空：{s["name"]}')
        if not groups or groups[-1]['edition'] != edition:
            if groups and edition == 'A':
                raise ValueError('A 版应排在 B 版前')
            label = 'A　今日动态 / 新闻层' if edition == 'A' else 'B　每日长期输入层'
            groups.append(dict(edition=edition, label=label, sections=[]))
        groups[-1]['sections'].append(s)
        for a in s['articles']:
            if not a.pop('paragraphs'):
                raise ValueError(f'{a["title"]} 缺少正文')
            a['body'] = Markup('\n').join(a.pop('parts'))
    return title, groups
