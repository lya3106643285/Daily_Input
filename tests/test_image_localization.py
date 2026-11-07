"""Offline image handoff, publication and hostile-download regression checks."""
from io import BytesIO
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import test_stage3_acceptance as acceptance
from publisher.images import download, inspect_image, prepare, public_addresses, url_parts
from publisher.ingest import ingest
from publisher.__main__ import publish
from publisher.manifest import digest, verify_ready

URL = 'https://images.example.org/picture.png'
RIGHTS = {'source_url': 'https://example.org/work', 'author': '测试作者',
          'title': '测试作品', 'license': 'CC0', 'license_url': 'https://creativecommons.org/publicdomain/zero/1.0/',
          'rights_verified_on': '2026-11-07', 'allow_offline': True}


def png():
    stream = BytesIO()
    Image.new('RGB', (60, 40), 'blue').save(stream, 'PNG')
    return stream.getvalue()


def receipt(url=URL):
    return {'url': url, 'final_url': url, 'redirects': [], 'content_type': 'image/png',
            'retrieved_at': '2026-11-07T00:00:00+00:00'}


class Handoff(unittest.TestCase):
    def setUp(self):
        self.fixture = acceptance.Stage3()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        f.add_body(f.material(URL))
        f.info['remote_images'] = {URL: dict(RIGHTS)}
        f.write()
        self.bundle = f.base / 'prepared'

    def prepare(self, **kwargs):
        return prepare(self.fixture.source, self.fixture.metadata, self.bundle, **kwargs)

    def test_remote_and_local_to_immutable_issue_and_offline_html(self):
        f = self.fixture
        f.image()
        f.add_body(f.material())
        f.write()
        original, metadata = f.source.read_bytes(), f.metadata.read_bytes()
        with patch('publisher.images.download', return_value=(png(), receipt())) as fetch:
            self.prepare()
            fetch.assert_called_once()
        issue = ingest(self.bundle/'draft.md', self.bundle/'issue.json', f.store)
        html = publish(issue, f.output, 1600, 12*1024*1024).read_text()
        self.assertEqual(f.source.read_bytes(), original)
        self.assertEqual(f.metadata.read_bytes(), metadata)
        self.assertEqual((issue/'original.md').read_bytes(), original)
        self.assertEqual((issue/'metadata-original.json').read_bytes(), metadata)
        self.assertFalse(acceptance.Page(html).external_resources)
        self.assertEqual(len(acceptance.Page(html).images), 2)
        self.assertIn('许可核验', html)
        self.assertIn('creativecommons.org', html)
        manifest = json.loads((issue/'manifest.json').read_text())
        record = next(r for r in manifest['asset_records'].values() if 'downloads' in r)
        self.assertEqual(record['downloads'][0]['sha256'], digest(png()))
        verify_ready(issue.resolve())
        before = issue.resolve()
        self.assertEqual(ingest(self.bundle/'draft.md', self.bundle/'issue.json', f.store).resolve(), before)

    def test_all_rights_checked_before_download(self):
        f = self.fixture
        other = 'https://other.example.org/image.png'
        f.add_body(f.material(other))
        f.info['remote_images'][other] = {**RIGHTS, 'allow_offline': False}
        f.write()
        with patch('publisher.images.download') as fetch, self.assertRaises(ValueError):
            self.prepare()
        fetch.assert_not_called()
        self.assertFalse(self.bundle.exists())

    def test_missing_license_rejected(self):
        self.fixture.info['remote_images'][URL].pop('license_url')
        self.fixture.write()
        with patch('publisher.images.download') as fetch, self.assertRaises(ValueError):
            self.prepare()
        fetch.assert_not_called()

    def test_corrupt_image_atomic_failure(self):
        with patch('publisher.images.download', return_value=(b'bad', receipt())), self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.bundle.exists())
        self.assertFalse(list(self.bundle.parent.glob('.images-*')))

    def test_existing_output_untouched(self):
        self.bundle.mkdir()
        keep = self.bundle/'keep'
        keep.write_text('existing')
        with patch('publisher.images.download') as fetch, self.assertRaises(ValueError):
            self.prepare()
        fetch.assert_not_called()
        self.assertEqual(keep.read_text(), 'existing')

    def test_bundle_tampering_rejected(self):
        with patch('publisher.images.download', return_value=(png(), receipt())):
            self.prepare()
        original = self.bundle/'author-original.md'
        original.write_bytes(original.read_bytes()+b'changed')
        with self.assertRaisesRegex(ValueError, '校验失败'):
            ingest(self.bundle/'draft.md', self.bundle/'issue.json', self.fixture.store)

    def test_download_file_tampering_rejected(self):
        with patch('publisher.images.download', return_value=(png(), receipt())):
            self.prepare()
        for asset in (self.bundle/'assets').iterdir():
            Image.new('RGB', (5, 5)).save(asset)
        with self.assertRaisesRegex(ValueError, '不一致'):
            ingest(self.bundle/'draft.md', self.bundle/'issue.json', self.fixture.store)

    def test_reference_style_and_code_are_distinguished(self):
        f = self.fixture
        f.text = f.text.replace(f'![测试图片替代文字]({URL})', '![测试图片替代文字][photo]')
        f.text += '\n[photo]: '+URL+'\n'
        f.add_body('模拟代码：\n\n```markdown\n![示例](https://do-not-fetch.example/a.png)\n```\n\n材料出处：测试构造。')
        f.write()
        with patch('publisher.images.download', return_value=(png(), receipt())) as fetch:
            self.prepare()
        fetch.assert_called_once()
        prepared = (self.bundle/'draft.md').read_text()
        self.assertIn('![示例](https://do-not-fetch.example/a.png)', prepared)
        self.assertIn('![测试图片替代文字](assets/', prepared)


    def test_multiline_alt_and_identity_line_numbers_preserved(self):
        f = self.fixture
        f.text = f.text.replace('![测试图片替代文字]', '![测试\n*图片*替代文字]')
        f.write()
        with patch('publisher.images.download', return_value=(png(), receipt())):
            self.prepare()
        prepared = (self.bundle/'draft.md').read_text()
        self.assertEqual(len(prepared.splitlines()), len(f.text.splitlines()))
        self.assertIn('![测试\n*图片*替代文字](assets/', prepared)
        issue = ingest(self.bundle/'draft.md', self.bundle/'issue.json', f.store)
        html = publish(issue, f.output, 1600, 12*1024*1024).read_text()
        self.assertEqual(acceptance.Page(html).images[0]['alt'], '测试\n*图片*替代文字')

    def test_total_and_count_limits(self):
        with patch('publisher.images.download', return_value=(png(), receipt())), self.assertRaises(ValueError):
            self.prepare(max_total_bytes=1)
        f = self.fixture
        f.add_body(f.material(URL))
        f.write()
        with patch('publisher.images.download') as fetch, self.assertRaises(ValueError):
            self.prepare(max_images=1)
        fetch.assert_not_called()

    def test_same_url_downloaded_once(self):
        self.fixture.add_body(self.fixture.material(URL))
        self.fixture.write()
        with patch('publisher.images.download', return_value=(png(), receipt())) as fetch:
            self.prepare()
        fetch.assert_called_once()

    def test_pixel_and_format_limits(self):
        with self.assertRaises(ValueError):
            inspect_image(png(), max_pixels=1)
        stream = BytesIO()
        Image.new('RGB', (2, 2)).save(stream, 'GIF')
        with self.assertRaises(ValueError):
            inspect_image(stream.getvalue())

    def test_inline_image_rejected(self):
        self.fixture.text = self.fixture.text.replace(f'![测试图片替代文字]({URL})', f'文内 ![图片]({URL})')
        self.fixture.write()
        with patch('publisher.images.download') as fetch, self.assertRaises(ValueError):
            self.prepare()
        fetch.assert_not_called()


class Network(unittest.TestCase):
    @staticmethod
    def addresses(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (host if host == '127.0.0.1' else '93.184.216.34', port))]

    def wire(self, raw, url='http://example.org/a', **kwargs):
        sock = Mock()
        sock.makefile.return_value = BytesIO(raw)
        with patch('publisher.images.socket.getaddrinfo', side_effect=self.addresses), patch('publisher.images.socket.socket', return_value=sock):
            result = download(url, **kwargs)
        return result, sock

    def test_valid_download_connects_to_validated_ip(self):
        data = png()
        result, sock = self.wire(b'HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: '+str(len(data)).encode()+b'\r\n\r\n'+data)
        self.assertEqual(result[0], data)
        sock.connect.assert_called_once_with(('93.184.216.34', 80))

    def test_direct_private_and_redirect_private_are_blocked(self):
        with patch('publisher.images.socket.getaddrinfo', side_effect=self.addresses), patch('publisher.images.socket.socket') as create:
            with self.assertRaisesRegex(ValueError, '非公网'):
                download('http://127.0.0.1/a')
            create.assert_not_called()
        with self.assertRaisesRegex(ValueError, '非公网'):
            self.wire(b'HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/private\r\nContent-Length: 0\r\n\r\n')

    def test_mixed_public_private_dns_rejected(self):
        entries = self.addresses('example.org', 80) + self.addresses('127.0.0.1', 80)
        with patch('publisher.images.socket.getaddrinfo', return_value=entries), self.assertRaises(ValueError):
            public_addresses('example.org', 80)

    def test_unsafe_urls_rejected(self):
        for url in ('file:///etc/passwd', 'http://user:pass@example.org/a', 'http://example.org:8080/a',
                    'https://example.org/a#frag', 'https://example.org/\\evil', 'http://example.org/a\nHeader:x'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                url_parts(url)

    def test_wrong_mime_status_and_oversized_stream(self):
        for response in (b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<html>',
                         b'HTTP/1.1 403 Forbidden\r\n\r\n',
                         b'HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: 99999\r\n\r\n',
                         b'HTTP/1.1 200 OK\r\nContent-Type: image/png\r\n\r\n123456'):
            with self.subTest(response=response), self.assertRaises(ValueError):
                self.wire(response, max_bytes=5)

    def test_redirect_limit(self):
        with self.assertRaisesRegex(ValueError, '重定向'):
            self.wire(b'HTTP/1.1 302 Found\r\nLocation: /again\r\nContent-Length: 0\r\n\r\n', redirects=0)


if __name__ == '__main__':
    unittest.main()
