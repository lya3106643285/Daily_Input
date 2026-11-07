"""Prepare an offline author handoff from reviewed image URLs.

python -m publisher.images draft.md --metadata issue.json --output-dir prepared
"""
import argparse
from datetime import date, datetime, timezone
from http.client import HTTPConnection, HTTPSConnection, HTTPException
from io import BytesIO
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
import time
from urllib.parse import urljoin, urlsplit, urlunsplit
import warnings

from PIL import Image
from markdown_it.tree import SyntaxTreeNode

from .assets import local_asset
from .content import MD, raw
from .manifest import digest, read_json

FORMATS = {'PNG': ('png', 'image/png'), 'JPEG': ('jpg', 'image/jpeg'),
           'WEBP': ('webp', 'image/webp')}


def url_parts(url):
    """Reject ambiguous authority, credentials, non-web schemes and odd ports."""
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url) or any(c in url for c in '\\<>\"'):
        raise ValueError('图片 URL 含非法字符')
    u = urlsplit(url)
    if (u.scheme not in ('http', 'https') or not u.hostname or u.username is not None
            or u.password is not None or u.fragment or u.port not in (None, 80, 443)):
        raise ValueError('需要无凭据、无片段的 HTTP(S) 图片 URL，端口限 80/443')
    return u


def public_addresses(host, port):
    addresses = list(dict.fromkeys(item[4][0] for item in
                     socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    def allowed(ip):
        address = ipaddress.ip_address(ip)
        return (address.is_global and not getattr(address, 'ipv4_mapped', None)
                and not getattr(address, 'sixtofour', None) and not getattr(address, 'teredo', None))
    if not addresses or not all(allowed(ip) for ip in addresses):
        raise ValueError('图片主机解析到非公网地址，拒绝下载')
    return addresses


def connect_public(host, port, timeout):
    # Connect to the exact validated address, never perform a second hostname lookup.
    addresses = public_addresses(host, port)
    last = None
    for ip in addresses:
        sock = socket.socket(socket.AF_INET6 if ':' in ip else socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((ip, port))
            return sock
        except OSError as exc:
            last = exc
            sock.close()
    raise OSError('无法连接图片主机') from last


class PublicHTTP(HTTPConnection):
    def connect(self):
        self.sock = connect_public(self.host, self.port, self.timeout)


class PublicHTTPS(HTTPSConnection):
    def connect(self):
        sock = connect_public(self.host, self.port, self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def download(url, *, max_bytes=8*1024*1024, timeout=20, redirects=4):
    """No proxies/cookies/auth; verify every redirect and pin every connection."""
    if max_bytes <= 0 or timeout <= 0 or redirects < 0:
        raise ValueError('下载大小、超时须大于零，重定向次数不得为负')
    current, chain = url, []
    deadline = time.monotonic() + timeout
    for hop in range(redirects + 1):
        u = url_parts(current)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('图片下载超时')
        host = u.hostname.encode('idna').decode('ascii')
        cls = PublicHTTPS if u.scheme == 'https' else PublicHTTP
        connection = cls(host, u.port, timeout=remaining)
        try:
            path = urlunsplit(('', '', u.path or '/', u.query, ''))
            connection.request('GET', path, headers={
                'User-Agent': 'DailyInput-ImageFetcher/1.0',
                'Accept': 'image/png,image/jpeg,image/webp', 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location or hop == redirects:
                    raise ValueError('图片重定向缺少目标或超过次数上限')
                target = urljoin(current, location)
                target_u = url_parts(target)
                if u.scheme == 'https' and target_u.scheme != 'https':
                    raise ValueError('拒绝 HTTPS 降级重定向')
                chain.append(current)
                current = target
                continue
            if response.status != 200:
                raise ValueError(f'图片服务器返回 HTTP {response.status}')
            content_type = response.getheader('Content-Type', '').split(';')[0].strip().lower()
            if content_type not in {v[1] for v in FORMATS.values()}:
                raise ValueError('需要静态 PNG、JPEG、WebP；网页或其他类型不下载')
            if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                raise ValueError('不接受压缩传输编码')
            length = response.getheader('Content-Length')
            if length is not None and (not length.isdecimal() or int(length) > max_bytes):
                raise ValueError('图片 Content-Length 非法或超过上限')
            data = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError('图片下载超时')
                # read1 performs at most one underlying read; bound slow streaming too.
                stream = getattr(response, 'fp', None)
                if stream and getattr(stream, 'raw', None):
                    stream.raw._sock.settimeout(remaining)
                chunk = response.read1(min(65536, max_bytes + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise ValueError('图片超过下载大小上限')
            if length is not None and len(data) != int(length):
                raise ValueError('图片下载不完整')
            return bytes(data), {'url': url, 'final_url': current,
                'redirects': chain, 'content_type': content_type,
                'retrieved_at': datetime.now(timezone.utc).isoformat()}
        except HTTPException as exc:
            raise ValueError('图片 HTTP 响应不完整或无效') from exc
        finally:
            connection.close()
    raise ValueError('图片重定向超过上限')


def inspect_image(data, max_pixels=25_000_000):
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        try:
            with Image.open(BytesIO(data)) as im:
                fmt, size = im.format, im.size
                if fmt not in FORMATS or getattr(im, 'is_animated', False):
                    raise ValueError('只接受静态 PNG、JPEG、WebP')
                if size[0] * size[1] > max_pixels:
                    raise ValueError('图片像素数超过上限')
                im.verify()
            with Image.open(BytesIO(data)) as im:
                im.load()
        except (OSError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
            raise ValueError('图片解码失败或像素数超过上限') from exc
    return fmt, size


def rights_record(record):
    if not isinstance(record, dict) or record.get('allow_offline') is not True:
        raise ValueError('每张远程图片须明确 allow_offline=true，并人工核对许可')
    for field in ('source_url', 'author', 'title', 'license', 'license_url', 'rights_verified_on'):
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise ValueError(f'图片许可记录缺少 {field}')
    url_parts(record['source_url'])
    url_parts(record['license_url'])
    date.fromisoformat(record['rights_verified_on'])
    if 'changes' in record and not isinstance(record['changes'], str):
        raise ValueError('图片改动说明须为字符串')
    return dict(record)


def plain(text):
    # Prevent supplied credit text from introducing Markdown links or HTML.
    import re
    return re.sub(r'([\\\x60*_{}\[\]()<>!#|])', r'\\\1', ' '.join(text.split()))


def prepare(author, metadata_path, destination, *, max_bytes=8*1024*1024,
            max_total_bytes=40*1024*1024, max_images=30, timeout=20, max_pixels=25_000_000):
    """Build a new immutable-by-convention bundle; never edit author input or overwrite output."""
    author, metadata_path, destination = map(lambda p: Path(p).resolve(),
                                            (author, metadata_path, destination))
    if min(max_bytes, max_total_bytes, max_images, timeout, max_pixels) <= 0:
        raise ValueError('图片限制须大于零')
    if destination.exists():
        raise ValueError('交稿包目录已存在；复用其中的文件，或选用新的目录')
    original = author.read_bytes()
    metadata_original = metadata_path.read_bytes()
    text = original.decode('utf-8-sig')
    metadata = read_json(metadata_path)
    if 'image_localization' in metadata:
        raise ValueError('输入已经本地化，请直接使用已有交稿包')
    lines = text.splitlines(keepends=True)
    nodes = SyntaxTreeNode(MD.parse(text)).children
    images, rights = [], {}
    if not isinstance(metadata.get('remote_images', {}), dict):
        raise ValueError('remote_images 须为以图片直链为键的对象')
    for index, node in enumerate(nodes):
        if node.type != 'paragraph':
            continue
        tokens = node.children[0].children
        if len(tokens) != 1 or tokens[0].type != 'image':
            if any(t.type == 'image' for t in tokens):
                raise ValueError(f'第 {node.map[0]+1} 行：图片须独立成段')
            continue
        ref = tokens[0].attrs['src']
        if not tokens[0].content.strip():
            raise ValueError('图片缺少替代文本')
        remote = ref.startswith(('http:', 'https:'))
        source = None
        if remote:
            url_parts(ref)
            rights[ref] = rights_record(metadata.get('remote_images', {}).get(ref))
            cursor = index + 1
            if cursor >= len(nodes) or not raw(nodes[cursor]).startswith('图注：'):
                raise ValueError('远程图片缺少紧邻图注')
            cursor += 1
            if cursor >= len(nodes) or not raw(nodes[cursor]).startswith('材料出处：'):
                raise ValueError('远程图片缺少紧邻材料出处')
            source = nodes[cursor].map[0]
        images.append((node, ref, tokens[0].content, source, remote))
    if len(images) > max_images:
        raise ValueError('图片数量超过上限')
    # Check all rights before the first network request.
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.images-', dir=destination.parent))
    records, copies, total = {}, {}, 0
    try:
        for node, ref, alt, source, remote in images:
            if not remote:
                from urllib.parse import unquote
                ref = unquote(ref)
            if ref not in copies:
                if remote:
                    data, receipt = download(ref, max_bytes=max_bytes, timeout=timeout)
                    fmt, size = inspect_image(data, max_pixels)
                    if receipt['content_type'] != FORMATS[fmt][1]:
                        raise ValueError('图片实际格式与 Content-Type 不一致')
                    local = f'assets/{digest(data)}.{FORMATS[fmt][0]}'
                    record = dict(rights[ref], **receipt, sha256=digest(data),
                                  bytes=len(data), width=size[0], height=size[1], path=local)
                    records[ref] = record
                else:
                    data = local_asset(author.parent, ref).read_bytes()
                    if len(data) > max_bytes:
                        raise ValueError('随期图片超过大小上限')
                    inspect_image(data, max_pixels)
                    local = ref
                total += len(data)
                if total > max_total_bytes:
                    raise ValueError('本期图片总大小超过上限')
                target = stage / local
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() and target.read_bytes() != data:
                    raise ValueError('图片路径发生内容冲突')
                target.write_bytes(data)
                copies[ref] = local
            local = copies[ref]
            if remote:
                start, end = node.map
                # Preserve line numbers used by metadata.identity.
                replacement = f'![{alt}]({local})'
                lines[start:end] = [replacement + '\n' * (end-start-replacement.count('\n'))] + [''] * (end-start-1)
                r = rights[ref]
                credit = (f" · {plain(r['author'])}，《{plain(r['title'])}》，"
                          f"[{plain(r['license'])}](<{r['license_url']}>)，"
                          f"[原始出处](<{r['source_url']}>)；许可核验 {r['rights_verified_on']}")
                if r.get('changes'):
                    credit += '；改动：' + plain(r['changes'])
                lines[source] = lines[source].rstrip('\r\n') + credit + '\n'
        prepared = ''.join(lines).encode('utf-8')
        metadata['image_localization'] = {
            'schema_version': 1, 'original_sha256': digest(original),
            'prepared_sha256': digest(prepared), 'metadata_original_sha256': digest(metadata_original), 'records': records}
        (stage / 'author-original.md').write_bytes(original)
        (stage / 'metadata-original.json').write_bytes(metadata_original)
        (stage / 'draft.md').write_bytes(prepared)
        (stage / 'issue.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2)+'\n', 'utf-8')
        if destination.exists():
            raise ValueError('交稿包目录已存在')
        stage.rename(destination)
        return destination
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('author', type=Path)
    cli.add_argument('--metadata', type=Path, required=True)
    cli.add_argument('--output-dir', type=Path, required=True)
    cli.add_argument('--max-bytes', type=int, default=8*1024*1024)
    cli.add_argument('--max-total-bytes', type=int, default=40*1024*1024)
    cli.add_argument('--max-images', type=int, default=30)
    cli.add_argument('--timeout', type=float, default=20)
    cli.add_argument('--max-pixels', type=int, default=25_000_000)
    args = cli.parse_args()
    try:
        print(prepare(args.author, args.metadata, args.output_dir, max_bytes=args.max_bytes,
                      max_total_bytes=args.max_total_bytes, max_images=args.max_images,
                      timeout=args.timeout, max_pixels=args.max_pixels))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        cli.exit(1, f'图片本地化失败：{exc}\n')


if __name__ == '__main__':
    main()
