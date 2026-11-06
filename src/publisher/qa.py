"""Validate one issue and its final HTML, including real offline browser screenshots."""
import argparse
import base64
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit

from PIL import Image, ImageChops

from .__main__ import publish
from .manifest import read_json, validate, verify_ready

VIEWPORTS = {'narrow': (390, 844), 'ipad-portrait': (820, 1180), 'ipad-landscape': (1180, 820)}
VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
STRUCTURAL = {'h1','h2','h3','h4','article','figure','table','pre','blockquote'}


class Document(HTMLParser):
    """Collect the publication's semantic content and enforce balanced element nesting."""
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.stack, self.tags, self.ids, self.links, self.images, self.resources = [], [], [], [], [], []
        self.body_text, self.styles, self.errors = [], [], []
        self.feed(text)
        self.close()
        if self.stack:
            self.errors.append('未闭合元素：' + ', '.join(self.stack))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append(tag)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'a':
            self.links.append(attrs.get('href',''))
        if tag == 'img':
            self.images.append(attrs)
        if tag in {'script','iframe','object','embed','video','audio','source','link'}:
            self.resources.append((tag, attrs.get('src', attrs.get('href', attrs.get('data','')))))
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f'元素闭合次序错误：{tag}')
        else:
            self.stack.pop()

    def handle_data(self, data):
        if 'style' in self.stack:
            self.styles.append(data)
        elif 'body' in self.stack and not {'script','head'} & set(self.stack):
            self.body_text.append(data)

    @property
    def text(self):
        return ' '.join(' '.join(self.body_text).split())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_structure(doc, manifest, expected):
    require(len(doc.ids) == len(set(doc.ids)) and all(doc.ids), '锚点重复或为空')
    missing = set(manifest['objects']) - set(doc.ids)
    require(not missing, f'缺少登记对象锚点：{sorted(missing)}')
    require(doc.tags.count('h1') == 1, '刊名 h1 应恰有一个')
    require([tag for tag in doc.tags if tag in STRUCTURAL] ==
            [tag for tag in expected.tags if tag in STRUCTURAL], '栏目/文章/材料元素层级或数量与原稿不符')
    level = 0
    for tag in doc.tags:
        if tag in {'h1','h2','h3','h4'}:
            current = int(tag[1])
            require(current <= level + 1, f'标题层级跳跃：{tag}')
            level = current
    refs = [url[1:] for url in doc.links if url.startswith('#')]
    require(set(refs) <= set(doc.ids), f'悬空页内链接：{sorted(set(refs)-set(doc.ids))}')
    return f'{doc.tags.count("h2")} 栏 / {doc.tags.count("article")} 篇 / {len(doc.ids)} 个唯一锚点'


def check_links(doc):
    for url in doc.links:
        parsed = urlsplit(url)
        valid = (url.startswith('#') and len(url) > 1 or
                 parsed.scheme in {'http','https'} and bool(parsed.netloc) or
                 bool(re.fullmatch(r'Daily_Input_\d{4}-\d{2}-\d{2}\.html#[A-Za-z][A-Za-z0-9_-]*',url)))
        require(valid and not re.search(r'\s',url), f'外链或资料链接格式错误：{url}')
    return f'{len(doc.links)} 个链接格式有效；未联网验证活性'


def check_media(doc, expected):
    require(len(doc.images) == len(expected.images), '图片引用数量与原稿不符')
    for img, original in zip(doc.images, expected.images):
        require(img.get('alt','').strip() and img.get('alt') == original.get('alt'), '图片替代文本缺失或与原稿不符')
        match = re.fullmatch(r'data:image/(jpeg|png|webp);base64,(.+)', img.get('src',''), re.S)
        require(match is not None, '图片未内嵌或格式不支持')
        with Image.open(BytesIO(base64.b64decode(match[2], validate=True))) as image:
            image.load()
            require(image.format.lower() == match[1], '图片声明类型与实际字节不符')
    for tag in ('figure','table','pre','blockquote','figcaption'):
        require(doc.tags.count(tag) == expected.tags.count(tag), f'材料组件缺失：{tag}')
    return f'{len(doc.images)} 张内嵌图片可解码；材料组件数量对应原稿'


def check_package(doc, data, maximum):
    require(0 < len(data) <= maximum, f'文件 {len(data)} 字节，超过上限 {maximum} 或为空')
    require(not doc.resources, f'单文件内存在外部或交互资源：{doc.resources}')
    for css in doc.styles:
        require(not re.search(r'@import\b|url\s*\(\s*[\'"]?(?!data:|#)', css, re.I), 'CSS 存在外部资源依赖')
    return f'{len(data)} 字节；CSS 与图片独立内嵌'


def browser_check(html_path, output, executable=None):
    from playwright.sync_api import sync_playwright
    result = {}
    with sync_playwright() as runtime:
        options = {'headless':True}
        if executable:
            options['executable_path'] = str(executable)
        browser = runtime.chromium.launch(**options)
        try:
            for name, (width,height) in VIEWPORTS.items():
                context = browser.new_context(viewport={'width':width,'height':height}, offline=True,
                                              device_scale_factor=1, reduced_motion='reduce')
                page = context.new_page()
                requests, errors = [], []
                page.on('request', lambda request: requests.append(request.url))
                page.on('pageerror', lambda error: errors.append(str(error)))
                try:
                    page.goto(html_path.as_uri(), wait_until='load')
                    page.evaluate('document.fonts.ready')
                    metrics = page.evaluate('''() => ({
                      width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
                      images: [...document.images].map(i => ({ok:i.complete && i.naturalWidth>0})),
                      brokenAnchors: [...document.querySelectorAll('a[href^="#"]')].filter(a => !document.getElementById(a.hash.slice(1))).map(a=>a.hash),
                      paragraphs: [...document.querySelectorAll('article p')].map(p => ({font:getComputedStyle(p).fontSize,line:getComputedStyle(p).lineHeight})),
                      title: document.title, height: document.documentElement.scrollHeight
                    })''')
                    page.screenshot(path=str(output/f'{name}.png'), full_page=True, animations='disabled')
                    external = [url for url in requests if not url.startswith(('file:','data:','about:'))]
                    require(not errors, f'{name} 浏览器脚本错误：{errors}')
                    require(not external, f'{name} 请求外部资源：{external}')
                    require(metrics['scrollWidth'] <= width+1, f'{name} 整页横向溢出：{metrics["scrollWidth"]}>{width}')
                    require(all(image['ok'] for image in metrics['images']), f'{name} 图片无法离线解码')
                    require(not metrics['brokenAnchors'], f'{name} 页内跳转缺失目标')
                    # Only article paragraphs use the accepted 20px/31px body settings.
                    require(metrics['paragraphs'] and all(p['font']=='20px' and p['line']=='31px' for p in metrics['paragraphs']),
                            f'{name} 正文字号或行高不符合20px/31px定稿')
                    result[name] = dict(metrics, viewport={'width':width,'height':height}, screenshot=f'{name}.png', external_requests=external)
                finally:
                    context.close()
        finally:
            browser.close()
    return result


def visual_check(output, baseline, update):
    if update:
        baseline.mkdir(parents=True, exist_ok=True)
        for name in VIEWPORTS:
            shutil.copyfile(output/f'{name}.png', baseline/f'{name}.png')
        (baseline/'baseline.json').write_text(json.dumps({'viewports':VIEWPORTS,'engine':'chromium'},indent=2)+'\n')
        return {'mode':'created','baseline_dir':str(baseline)}
    require((baseline/'baseline.json').is_file(), '缺少视觉基线，请明确使用 --update-baseline 建立')
    differences = {}
    for name in VIEWPORTS:
        reference = baseline/f'{name}.png'
        require(reference.is_file(), f'视觉基线缺少 {name}.png')
        with Image.open(reference) as before, Image.open(output/f'{name}.png') as after:
            if before.size != after.size:
                differences[name] = {'size_before':before.size,'size_after':after.size,'changed_ratio':1}
                continue
            diff = ImageChops.difference(before.convert('RGB'),after.convert('RGB'))
            # Ignore small per-channel rasterization variations while exposing real layout/color changes.
            channels = diff.split()
            mask = ImageChops.lighter(ImageChops.lighter(channels[0],channels[1]),channels[2]).point(lambda n:255 if n>16 else 0)
            changed = mask.histogram()[255]/(after.width*after.height)
            differences[name] = {'changed_ratio':changed,'maximum':0.001}
            if changed > 0.001:
                diff.save(output/f'{name}-diff.png')
    require(all(info['changed_ratio'] <= 0.001 for info in differences.values()), f'视觉差异超限：{differences}')
    return {'mode':'compared','baseline_dir':str(baseline),'viewports':differences}


def run_qa(issue, html_path, output, baseline, *, update_baseline=False, max_bytes=12*1024*1024, browser_executable=None):
    issue, html_path, output, baseline = (Path(p).resolve() for p in (issue,html_path,output,baseline))
    output.mkdir(parents=True,exist_ok=True)
    report = {'status':'failed','issue':str(issue),'html':str(html_path),'checks':[], 'browser':{'viewports':{}}}

    def check(name, action):
        try:
            details = action()
            report['checks'].append({'id':name,'status':'passed','details':details})
            return details
        except Exception as error:
            report['checks'].append({'id':name,'status':'failed','details':str(error)})
            return None

    try:
        verify_ready(issue)
        manifest = validate(read_json(issue/'manifest.json'))
        data = html_path.read_bytes()
        text = data.decode('utf-8')
        doc = Document(text)
        with tempfile.TemporaryDirectory(prefix='daily-qa-reference-') as temp:
            expected_file = publish(issue,Path(temp),1600,1024*1024*1024)
            expected = Document(expected_file.read_text('utf-8'))
        check('content', lambda: (require(doc.text == expected.text, '成品正文、来源或目录文本与本期工作副本不一致') or '成品语义文本与本期原稿完全对应'))
        check('structure',lambda:check_structure(doc,manifest,expected))
        check('media',lambda:check_media(doc,expected))
        check('links',lambda:check_links(doc))
        check('html',lambda:(require(not doc.errors and '<!doctype html>' in text.lower() and
                                   all(doc.tags.count(tag)==1 for tag in ('html','head','body')),
                                   f'HTML 结构错误：{doc.errors}') or 'HTML文档完整，元素正确闭合'))
        check('package',lambda:check_package(doc,data,max_bytes))
        if all(item['status']=='passed' for item in report['checks']):
            metrics = check('browser',lambda:browser_check(html_path,output,browser_executable))
            if metrics is not None:
                report['browser']['viewports'] = metrics
                check('visual',lambda:visual_check(output,baseline,update_baseline))
            else:
                report['checks'].append({'id':'visual','status':'not_run','details':'浏览器失败，未创建或修改视觉基线'})
        else:
            for name in ('browser','visual'):
                report['checks'].append({'id':name,'status':'not_run','details':'静态检查未通过，未运行浏览器或修改基线'})
        if len(report['checks'])==8 and all(item['status']=='passed' for item in report['checks']):
            report['status']='passed'
    except Exception as error:
        report['checks'].append({'id':'input','status':'failed','details':str(error)})
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines = [f'# 自动质检：{report["status"]}',f'\nHTML：{html_path}', '\n| 检查 | 结果 | 说明 |','|---|---|---|']
    for item in report['checks']:
        if item['id']=='browser' and item['status']=='passed':
            detail = '；'.join(f'[{name}]({name}.png) {info["viewport"]["width"]}×{info["viewport"]["height"]}，无溢出、图片可解码、内链有效' for name,info in report['browser']['viewports'].items())
        elif item['id']=='visual' and item['status']=='passed':
            detail = '已显式建立基线' if item['details']['mode']=='created' else '三视口视觉基线比较通过'
        else:
            detail = str(item['details']).replace('|','/').replace('\n',' ')
        lines.append(f'| {item["id"]} | {item["status"]} | {detail} |')
    (output/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return report


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('issue',type=Path)
    cli.add_argument('--html',type=Path,required=True)
    cli.add_argument('--output-dir',type=Path,required=True)
    cli.add_argument('--baseline-dir',type=Path,required=True)
    cli.add_argument('--update-baseline',action='store_true',help='显式创建或更新视觉基线（其他检查须先通过）')
    cli.add_argument('--browser-executable',type=Path)
    cli.add_argument('--max-bytes',type=int,default=12*1024*1024)
    args=cli.parse_args()
    report=run_qa(args.issue,args.html,args.output_dir,args.baseline_dir,update_baseline=args.update_baseline,
                  max_bytes=args.max_bytes,browser_executable=args.browser_executable)
    print(f'{report["status"]}: {args.output_dir / "report.json"}')
    raise SystemExit(0 if report['status']=='passed' else 1)


if __name__=='__main__':
    main()
