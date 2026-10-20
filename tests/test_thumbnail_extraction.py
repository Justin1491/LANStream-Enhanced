"""Video thumbnail checks use isolated fictional media and mocked FFmpeg."""
import os
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


class ThumbnailExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        cls.root = Path(cls.temp.name)
        config = cls.root / 'config.txt'
        config.write_text('port=8061\n', encoding='utf-8')
        cls.imported_app = 'app' not in sys.modules
        with patch.dict(os.environ, {'LANSTREAM_CONFIG_FILE': str(config),
                                    'LANSTREAM_CACHE_DIR': str(cls.root / 'cache')}):
            import app
        cls.server = app

    @classmethod
    def tearDownClass(cls):
        if cls.imported_app:
            cls.server.directory_index.close()
            cls.server.image_scheduler.close()
        cls.temp.cleanup()

    def setUp(self):
        self.source = self.root / 'fictional.mp4'
        self.source.write_bytes(b'fictional media')
        self.output = self.root / 'thumbnail.jpg'
        self.output.unlink(missing_ok=True)
        self.tmp = self.output.with_suffix('.tmp.jpg')
        self.scheduler = Mock()
        self.scheduler.is_current.return_value = True

    def generate(self, run, candidates=(2.0, 4.0), width=360):
        with patch.object(self.server, 'probe', return_value={}), \
             patch.object(self.server, 'screenshot_candidates', return_value=list(candidates)), \
             patch.object(self.server.subprocess, 'run', side_effect=run) as process:
            self.server.generate_image(self.source, self.output, width, 7, self.scheduler)
        return process

    def extracted(self, cmd, percent=0, returncode=0, **kwargs):
        if cmd[-1] != '-':
            Path(cmd[-1]).write_bytes(f'frame {cmd[cmd.index("-ss") + 1]}'.encode())
        stderr = f'[Parsed_blackframe_0] frame:0 pblack:{percent}'
        if not kwargs.get('text'):
            stderr = stderr.encode()
        return subprocess.CompletedProcess(cmd, returncode, b'', stderr)

    def test_one_process_checks_unscaled_blackness_and_extracts_thumbnail_or_poster(self):
        for width in (360, 1280):
            with self.subTest(width=width):
                process = self.generate(self.extracted, width=width)
                self.assertEqual(process.call_count, 1)
                cmd = process.call_args.args[0]
                self.assertEqual(cmd[cmd.index('-loglevel') + 1], 'info')
                self.assertEqual(cmd[cmd.index('-vf') + 1],
                                 f'blackframe=amount=0:threshold=32,scale={width}:-2:force_original_aspect_ratio=decrease')
                self.assertEqual(cmd[-1], str(self.tmp))
                self.assertEqual(self.output.read_bytes(), b'frame 2.000')
                self.assertFalse(self.tmp.exists())

    def test_black_candidate_is_skipped_and_next_candidate_is_used(self):
        def run(cmd, **kwargs):
            return self.extracted(cmd, 85 if cmd[cmd.index('-ss') + 1] == '2.000' else 84, **kwargs)
        process = self.generate(run)
        self.assertEqual(process.call_count, 2)
        self.assertEqual(self.output.read_bytes(), b'frame 4.000')
        self.assertFalse(self.tmp.exists())

    def test_all_black_candidates_keep_first_candidate_fallback(self):
        process = self.generate(lambda cmd, **kwargs: self.extracted(cmd, 100, **kwargs))
        self.assertEqual(process.call_count, 3)
        self.assertEqual(self.output.read_bytes(), b'frame 2.000')
        self.assertFalse(self.tmp.exists())

    def test_missing_black_metadata_still_accepts_successful_extraction(self):
        def run(cmd, **kwargs):
            result = self.extracted(cmd)
            result.stderr = b''
            return result
        process = self.generate(run)
        self.assertEqual(process.call_count, 1)
        self.assertTrue(self.output.exists())

    def test_failed_process_cannot_publish_partial_output(self):
        process = self.generate(lambda cmd, **kwargs: self.extracted(cmd, returncode=1))
        self.assertEqual(process.call_count, 3)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.tmp.exists())

    def test_exception_cleans_partial_output_and_marks_failure(self):
        def run(cmd, **kwargs):
            self.tmp.write_bytes(b'partial')
            raise subprocess.TimeoutExpired(cmd, 90)
        self.generate(run)
        self.scheduler.mark_failed.assert_called_once_with(7, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.tmp.exists())

    def test_changed_source_cannot_publish_thumbnail_or_fallback(self):
        original = self.source.read_bytes()
        self.scheduler.is_current.side_effect = lambda *args: self.source.read_bytes() == original
        def run(cmd, **kwargs):
            result = self.extracted(cmd)
            self.source.write_bytes(b'replacement media')
            return result
        self.generate(run)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.tmp.exists())

    def test_large_jpeg_is_reduced_before_orientation_processing(self):
        image = self.server.Image.new('RGB', (4000, 3000), 'red')
        data = io.BytesIO()
        image.save(data, 'JPEG')
        image.close()
        transpose = self.server.ImageOps.exif_transpose
        sizes = []
        def record(image):
            sizes.append(image.size)
            return transpose(image)
        with self.server.Image.open(io.BytesIO(data.getvalue())) as image, \
             patch.object(self.server.ImageOps, 'exif_transpose', side_effect=record):
            self.server._save_pillow_jpeg(image, self.output, 360)
        self.assertTrue(all(width <= 360 and height <= 720 for width, height in sizes), sizes)
        with self.server.Image.open(self.output) as image:
            self.assertEqual(image.size, (360, 270))

    def test_thumbnail_preserves_all_exif_orientations_and_bounds(self):
        colors = ['red', 'green', 'yellow', 'blue', 'red', 'blue', 'yellow', 'green']
        for orientation, color in enumerate(colors, 1):
            with self.subTest(orientation=orientation):
                image = self.server.Image.new('RGB', (1200, 600), 'red')
                image.paste('green', (600, 0, 1200, 300))
                image.paste('blue', (0, 300, 600, 600))
                image.paste('yellow', (600, 300, 1200, 600))
                image.getexif()[274] = orientation
                self.server._save_pillow_jpeg(image, self.output, 360)
                image.close()
                with self.server.Image.open(self.output) as result:
                    self.assertEqual(result.size, (360, 180) if orientation < 5 else (360, 720))
                    self.assertNotIn(274, result.getexif())
                    expected = self.server.Image.new('RGB', (1, 1), color).getpixel((0, 0))
                    actual = result.getpixel((20, 20))
                    self.assertLess(max(abs(a - b) for a, b in zip(actual, expected)), 10)

    def test_encoded_rotated_images_preserve_dimensions_and_orientation(self):
        for format in ('JPEG', 'PNG', 'TIFF'):
            for size, expected in [((1200, 600), (360, 720)), ((600, 1200), (360, 180))]:
                with self.subTest(format=format, size=size):
                    image = self.server.Image.new('RGB', size, 'red')
                    image.paste('blue', (0, size[1] // 2, *size))
                    exif = image.getexif()
                    exif[274] = 6
                    data = io.BytesIO()
                    image.save(data, format, exif=exif)
                    image.close()
                    with self.server.Image.open(io.BytesIO(data.getvalue())) as source:
                        self.server._save_pillow_jpeg(source, self.output, 360)
                    with self.server.Image.open(self.output) as result:
                        self.assertEqual(result.size, expected)
                        self.assertGreater(result.getpixel((20, 20))[2], 240)
                        self.assertGreater(result.getpixel((340, 20))[0], 240)

    def test_thumbnail_preserves_transparency_background_and_grayscale(self):
        for mode, color, expected in [('RGBA', (255, 0, 0, 0), (0, 0, 0)),
                                      ('L', 128, (128, 128, 128))]:
            with self.subTest(mode=mode):
                image = self.server.Image.new(mode, (40, 60), color)
                self.server._save_pillow_jpeg(image, self.output, 360)
                image.close()
                with self.server.Image.open(self.output) as result:
                    self.assertEqual(result.size, (40, 60))
                    self.assertEqual(result.mode, 'RGB')
                    self.assertEqual(result.getpixel((20, 20)), expected)

    def test_bundled_ffmpeg_skips_real_black_frame_and_extracts_red_frame(self):
        ffmpeg = Path(__file__).resolve().parents[1] / 'tools' / 'ffmpeg.exe'
        if not ffmpeg.is_file():
            self.skipTest('bundled FFmpeg is unavailable')
        subprocess.run([
            str(ffmpeg), '-hide_banner', '-loglevel', 'error',
            '-f', 'lavfi', '-i', 'color=black:s=64x48:r=5:d=2',
            '-f', 'lavfi', '-i', 'color=red:s=64x48:r=5:d=2',
            '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0',
            '-c:v', 'mpeg4', '-y', str(self.source),
        ], capture_output=True, check=True, timeout=30)
        real_run = subprocess.run
        with patch.object(self.server, 'FFMPEG', str(ffmpeg)):
            process = self.generate(lambda *args, **kwargs: real_run(*args, **kwargs),
                                    candidates=(0.0, 2.5))
        self.assertEqual(process.call_count, 2)
        with self.server.Image.open(self.output) as image:
            red, green, blue = image.convert('RGB').getpixel((0, 0))
            self.assertGreater(red, 200)
            self.assertLess(max(green, blue), 30)
        self.assertFalse(self.tmp.exists())


if __name__ == '__main__':
    unittest.main()
