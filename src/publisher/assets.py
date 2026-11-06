"""Build offline reading copies while preserving the issue's source files."""
import base64
from io import BytesIO
from pathlib import Path
import warnings

from PIL import Image, ImageOps


def local_asset(root, reference):
    path = Path(reference)
    if path.is_absolute() or path.parts[:1] != ('assets',) or '..' in path.parts:
        raise ValueError(f'图片须使用 assets/ 下的相对路径：{reference}')
    target = (root / path).resolve()
    if not target.is_relative_to(root / 'assets') or not target.is_file():
        raise ValueError(f'缺少随期图片或图片越界：{reference}')
    return target


def image_uri(path, max_edge):
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(path) as source:
            fmt = source.format
            if fmt not in {'PNG', 'JPEG', 'WEBP'} or getattr(source, 'is_animated', False):
                raise ValueError(f'需要静态 PNG、JPEG 或 WebP：{path.name}')
            im = ImageOps.exif_transpose(source)
            original_size = im.size
            im.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            out = BytesIO()
            options = {'PNG': {'optimize': True}, 'JPEG': {'quality': 85, 'optimize': True},
                       'WEBP': {'quality': 85, 'method': 6}}[fmt]
            if fmt == 'JPEG' and im.mode != 'RGB':
                im = im.convert('RGB')
            im.save(out, format=fmt, icc_profile=source.info.get('icc_profile'), **options)
            data = out.getvalue()
            if (im.size == original_size and source.getexif().get(274, 1) == 1
                    and path.stat().st_size < len(data)):
                data = path.read_bytes()
    return f'data:image/{fmt.lower()};base64,' + base64.b64encode(data).decode('ascii')
