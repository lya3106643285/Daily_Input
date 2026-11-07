"""Validate one issue and its final HTML, including real offline browser screenshots."""
import argparse
import base64
import hashlib
from html.parser import HTMLParser
from io import BytesIO
import json
import platform
import subprocess
from importlib.metadata import version
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit

from PIL import Image, ImageChops

from .__main__ import publish
from .manifest import read_json, validate, verify_ready
from .fonts import verify_fonts

VIEWPORTS = {'narrow': (390, 844), 'ipad-portrait': (820, 1180), 'ipad-landscape': (1180, 820)}
VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
STRUCTURAL = {'h1','h2','h3','h4','article','figure','table','pre','blockquote'}


class Document(HTMLParser):
    """Collect the publication's semantic content and enforce balanced element nesting."""
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.stack, self.tags, self.ids, self.links, self.images, self.resources = [], [], [], [], [], []
        self.body_text, self.styles, self.errors = [], [], []
        self.id_elements = []
        self.feed(text)
        self.close()
        if self.stack:
            self.errors.append('未闭合元素：' + ', '.join(self.stack))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append(tag)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
            self.id_elements.append((tag, attrs['id']))
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


def check_content(doc, expected):
    require(doc.text == expected.text, '成品正文、来源或目录文本与本期工作副本不一致')
    require(doc.links == expected.links, '成品链接目标或对应次序与本期工作副本不一致')
    return '成品语义文本和链接目标与本期原稿完全对应'


def check_structure(doc, manifest, expected):
    require(len(doc.ids) == len(set(doc.ids)) and all(doc.ids), '锚点重复或为空')
    require(doc.id_elements == expected.id_elements, '锚点与栏目、文章或材料对象的绑定关系不符')
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


def capture_screenshot(page, destination):
    """Retry only Chromium's transient capture error; all other failures propagate."""
    from playwright.sync_api import Error
    for attempt in range(1, 4):
        try:
            page.screenshot(path=str(destination), full_page=True, animations='disabled')
            return attempt
        except Error as error:
            if 'Unable to capture screenshot' not in str(error) or attempt == 3:
                raise
            page.evaluate('''() => new Promise(resolve =>
                requestAnimationFrame(() => requestAnimationFrame(resolve)))''')


def browser_check(html_path, output, executable=None):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError(
            '缺少 Playwright；请在 daily-input 环境执行 python -m pip install -r requirements-qa.txt'
        ) from error
    fonts = verify_fonts()
    environment = {
        'fonts': fonts,
        'playwright': version('playwright'),
        'platform': platform.system(),
        'architecture': platform.machine(),
        'fontconfig': subprocess.check_output(['fc-match', '--version'], stderr=subprocess.STDOUT, text=True).strip(),
    }
    # Package versions make rendering changes diagnosable without recording home paths.
    try:
        environment['font_packages'] = subprocess.check_output(
            ['dpkg-query', '-W', '-f=' + chr(36) + '{Package}=' + chr(36) + '{Version}\n',
             'libfontconfig1', 'libfreetype6'], text=True, stderr=subprocess.DEVNULL).strip().splitlines()
    except (OSError, subprocess.CalledProcessError):
        environment['font_packages'] = []
    result = {}
    with sync_playwright() as runtime:
        options = {'headless':True}
        if executable:
            options['executable_path'] = str(executable)
        try:
            browser = runtime.chromium.launch(**options)
        except Exception as error:
            raise RuntimeError(
                'Chromium 启动失败。请确认已激活 daily-input 环境，执行 '
                'python -m playwright install chromium；'
                'Ubuntu 缺少共享库时，在本机终端执行 '
                'python -m playwright install-deps chromium（需要 sudo 授权）。'
                '若出现 Operation not permitted / socket 权限错误，须在允许启动浏览器的环境重跑；'
                '不能通过跳过检查或更新基线解决。原始错误：' + str(error)
            ) from error
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
                    screenshot_attempts = capture_screenshot(page, output/f'{name}.png')
                    external = [url for url in requests if not url.startswith(('file:','data:','about:'))]
                    require(not errors, f'{name} 浏览器脚本错误：{errors}')
                    require(not external, f'{name} 请求外部资源：{external}')
                    require(metrics['scrollWidth'] <= width+1, f'{name} 整页横向溢出：{metrics["scrollWidth"]}>{width}')
                    require(all(image['ok'] for image in metrics['images']), f'{name} 图片无法离线解码')
                    require(not metrics['brokenAnchors'], f'{name} 页内跳转缺失目标')
                    # Only article paragraphs use the accepted 20px/31px body settings.
                    require(metrics['paragraphs'] and all(p['font']=='20px' and p['line']=='31px' for p in metrics['paragraphs']),
                            f'{name} 正文字号或行高不符合20px/31px定稿')
                    result[name] = dict(metrics, screenshot_attempts=screenshot_attempts, browser_version=browser.version, render_environment=dict(environment, chromium=browser.version), viewport={'width':width,'height':height}, screenshot=f'{name}.png', external_requests=external)
                finally:
                    context.close()
        finally:
            browser.close()
    return result


def visual_check(output, baseline, update, environment=None, identity=None):
    if update:
        baseline.mkdir(parents=True, exist_ok=True)
        for name in VIEWPORTS:
            shutil.copyfile(output/f'{name}.png', baseline/f'{name}.png')
        metadata = {
            'viewports': VIEWPORTS, 'engine': 'chromium', 'environment': environment,
            'identity': identity,
            'screenshot_sha256': {
                name: hashlib.sha256((baseline/f'{name}.png').read_bytes()).hexdigest()
                for name in VIEWPORTS
            },
        }
        (baseline/'baseline.json').write_text(json.dumps(metadata,indent=2)+'\n')
        return {'mode':'created','baseline_dir':str(baseline)}
    require((baseline/'baseline.json').is_file(), '缺少视觉基线，请明确使用 --update-baseline 建立')
    metadata = json.loads((baseline/'baseline.json').read_text())
    if metadata.get('identity') is not None:
        require(metadata['identity'] == identity,
                '视觉基线不属于当前稿件或 HTML：每日不同内容、修订和样式更改需独立审阅；不能复用其他期次基线')
    if metadata.get('screenshot_sha256') is not None:
        for name in VIEWPORTS:
            reference = baseline/f'{name}.png'
            require(reference.is_file() and hashlib.sha256(reference.read_bytes()).hexdigest() ==
                    metadata['screenshot_sha256'].get(name),
                    f'视觉基线 {name}.png 与登记哈希不一致，须检查基线完整性')
    if metadata.get('environment') is not None:
        require(metadata['environment'] == environment,
                '视觉环境与基线不同；请检查字体锁、Chromium、Playwright、fontconfig 和 FreeType 版本，不能自动更新基线')
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


def run_qa(issue, html_path, output, baseline=None, *, update_baseline=False, max_bytes=12*1024*1024, browser_executable=None, visual_mode='compare'):
    if visual_mode not in {'compare', 'rules'}:
        raise ValueError('visual_mode 须为 compare 或 rules')
    if visual_mode == 'rules' and (baseline is not None or update_baseline):
        raise ValueError('规则模式不读取或更新基线，请省略 baseline 和 update_baseline')
    if visual_mode == 'compare' and baseline is None:
        raise ValueError('同一期比较模式须提供 baseline')
    issue, html_path, output = (Path(p).resolve() for p in (issue, html_path, output))
    baseline = Path(baseline).resolve() if baseline is not None else None
    if visual_mode == 'rules' and (output / 'baseline.json').exists():
        raise ValueError('规则截图输出目录含视觉基线；为保护基线，未写入任何文件')
    # A screenshot output must never overwrite the references it is checking.
    if baseline is not None and (output == baseline or output in baseline.parents or baseline in output.parents):
        raise ValueError('QA 输出目录与视觉基线目录不得相同或相互包含；为保护基线，未写入任何报告或截图')
    output.mkdir(parents=True,exist_ok=True)
    report = {'status':'failed','issue':str(issue),'html':str(html_path),'checks':[], 'browser':{'viewports':{}}}
    report.update(visual_mode=visual_mode, human_visual_review='not_performed')

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
        identity = {
            'date': manifest['date'], 'version': manifest['version'],
            'manifest_sha256': hashlib.sha256((issue/'manifest.json').read_bytes()).hexdigest(),
            'html_sha256': hashlib.sha256(data).hexdigest(),
        }
        report['baseline_identity'] = identity
        text = data.decode('utf-8')
        doc = Document(text)
        with tempfile.TemporaryDirectory(prefix='daily-qa-reference-') as temp:
            expected_file = publish(issue,Path(temp),1600,1024*1024*1024)
            expected = Document(expected_file.read_text('utf-8'))
        check('content', lambda:check_content(doc,expected))
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
                if visual_mode == 'compare':
                    check('visual',lambda:visual_check(output,baseline,update_baseline, next(iter(metrics.values()), {}).get('render_environment'), identity))
                else:
                    report['checks'].append({'id': 'visual', 'status': 'not_applicable',
                        'details': '每日规则检查：保留三视口截图；不与其他期次整页比较，未进行人工审美验收'})
            else:
                report['checks'].append({'id':'visual','status':'not_run','details':'浏览器失败，未创建或修改视觉基线'})
        else:
            for name in ('browser','visual'):
                report['checks'].append({'id':name,'status':'not_run','details':'静态检查未通过，未运行浏览器或修改基线'})
        if len(report['checks']) == 8 and all(
                item['status'] == 'passed' or
                (visual_mode == 'rules' and item['id'] == 'visual' and item['status'] == 'not_applicable')
                for item in report['checks']):
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
    cli.add_argument('--baseline-dir',type=Path)
    cli.add_argument('--rules-only',action='store_true',help='每日规则检查并保存截图，不读取或更新视觉基线')
    cli.add_argument('--update-baseline',action='store_true',help='显式创建或更新视觉基线（其他检查须先通过）')
    cli.add_argument('--browser-executable',type=Path)
    cli.add_argument('--max-bytes',type=int,default=12*1024*1024)
    args=cli.parse_args()
    if args.rules_only and (args.baseline_dir or args.update_baseline):
        cli.error('--rules-only 不能同时提供基线或 --update-baseline')
    if not args.rules_only and args.baseline_dir is None:
        cli.error('同一期比较须提供 --baseline-dir，或使用 --rules-only')
    report=run_qa(args.issue,args.html,args.output_dir,args.baseline_dir,update_baseline=args.update_baseline,
                  max_bytes=args.max_bytes,browser_executable=args.browser_executable,
                  visual_mode='rules' if args.rules_only else 'compare')
    print(f'{report["status"]}: {args.output_dir / "report.json"}')
    raise SystemExit(0 if report['status']=='passed' else 1)


if __name__=='__main__':
    main()
