"""Small fictional media shared by route tests and browser checks."""
import io
from pathlib import Path
import shutil
import struct
import zipfile
import zlib

import fitz
from PIL import Image, ImageDraw


def _picture(color, label, size=(240, 320), format='PNG'):
    image = Image.new('RGB', size, color)
    ImageDraw.Draw(image).text((16, 20), label, fill='white')
    output = io.BytesIO()
    image.save(output, format=format)
    return output.getvalue()


def _rar_header(kind, flags, body=b''):
    header = struct.pack('<BHH', kind, flags, 7 + len(body)) + body
    return struct.pack('<H', zlib.crc32(header) & 0xffff) + header


def _write_rar(path, members):
    # Stored RAR4 blocks keep this real archive fixture independent of a RAR writer.
    output = bytearray(b'Rar!\x1a\x07\x00')
    output += _rar_header(0x73, 0, b'\0' * 6)
    for name, data in members:
        encoded = name.encode('ascii')
        body = struct.pack('<IIBIIBBHI', len(data), len(data), 2,
                           zlib.crc32(data), 0, 20, 0x30, len(encoded), 0x20)
        output += _rar_header(0x74, 0x8000, body + encoded) + data
    output += _rar_header(0x7b, 0)
    path.write_bytes(output)


def create_viewer_fixtures(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    (root / 'Images').mkdir(exist_ok=True)
    (root / 'Private').mkdir(exist_ok=True)
    paths = {
        'image_first': "Images/01 Café #1 & friend's.png",
        'image_next': 'Images/02 Landscape.jpg',
        'image_tiff': 'Images/03 Portrait.tiff',
        'pdf': 'Field guide Café #1 & notes.pdf',
        'cbz': 'Color story #1.cbz', 'cbr': 'Color story #2.cbr',
        'text': "Café & friend's notes.txt", 'markdown': 'Read me #1.md',
        'corrupt_pdf': 'Broken.pdf', 'corrupt_cbz': 'Broken.cbz',
        'corrupt_cbr': 'Broken.cbr', 'corrupt_image': 'Broken.tiff',
        'corrupt_page_cbz': 'Broken page.cbz',
        'empty_cbz': 'Empty.cbz',
    }
    (root / paths['image_first']).write_bytes(_picture('firebrick', 'IMAGE 1'))
    (root / paths['image_next']).write_bytes(_picture('forestgreen', 'IMAGE 2', (360, 220), 'JPEG'))
    (root / paths['image_tiff']).write_bytes(_picture('navy', 'TIFF IMAGE 3', format='TIFF'))
    with fitz.open() as document:
        for index, (width, height, color) in enumerate([
            (300, 440, (0.7, 0.1, 0.1)), (460, 280, (0.1, 0.6, 0.1)),
            (320, 460, (0.1, 0.1, 0.7)),
        ], 1):
            page = document.new_page(width=width, height=height)
            page.draw_rect(page.rect, color=color, fill=color)
            page.insert_text((24, 48), f'FICTIONAL FIELD GUIDE - PAGE {index}', fontsize=12, color=(1, 1, 1))
        document.save(root / paths['pdf'], no_new_id=True)
    members = [
        ('page10.png', _picture('navy', 'COMIC PAGE 10')),
        ('page2.jpg', _picture('forestgreen', 'COMIC PAGE 2', (360, 220), 'JPEG')),
        ('page1.png', _picture('firebrick', 'COMIC PAGE 1')),
    ]
    with zipfile.ZipFile(root / paths['cbz'], 'w') as archive:
        for name, data in members:
            archive.writestr(zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0)), data)
    _write_rar(root / paths['cbr'], members)
    for key in ('text', 'markdown'):
        (root / paths[key]).write_text('# Fictional Café notes\n<script>alert("fixture")</script>\nA & B <example>\n', encoding='utf-8')
    for key in ('corrupt_pdf', 'corrupt_cbz', 'corrupt_cbr', 'corrupt_image'):
        (root / paths[key]).write_bytes(b'fictional invalid media\n')
    with zipfile.ZipFile(root / paths['empty_cbz'], 'w') as archive:
        archive.writestr(zipfile.ZipInfo('notes.txt', date_time=(2020, 1, 1, 0, 0, 0)), 'No comic images here.')
    with zipfile.ZipFile(root / paths['corrupt_page_cbz'], 'w') as archive:
        archive.writestr(zipfile.ZipInfo('page1.png', date_time=(2020, 1, 1, 0, 0, 0)), b'fictional invalid image')
    for kind, source in [('image', 'image_first'), ('pdf', 'pdf'), ('cbz', 'cbz'), ('cbr', 'cbr'), ('text', 'text')]:
        private = 'Private/' + Path(paths[source]).name
        shutil.copyfile(root / paths[source], root / private)
        paths['private_' + kind] = private
    return paths
