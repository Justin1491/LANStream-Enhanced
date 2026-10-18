"""Reproducible synthetic benchmark, or read-only benchmark of an explicit root."""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ItemCounter(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = 0
        self.elements = 0

    def handle_starttag(self, tag, attrs):
        self.elements += 1
        classes = dict(attrs).get('class', '').split()
        if 'filter-item' in classes or 'explorer-item' in classes:
            self.items += 1


def percentile(values, fraction=.95):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def measure(function, repeats):
    samples = []
    result = None
    for _ in range(repeats):
        start = time.perf_counter()
        result = function()
        samples.append((time.perf_counter() - start) * 1000)
    return {'p50_ms': round(statistics.median(samples), 3),
            'p95_ms': round(percentile(samples), 3)}, result


def benchmark(root, work, repeats, trace_memory=False):
    os.environ['LANSTREAM_CONFIG_FILE'] = str(work / 'config.txt')
    os.environ['LANSTREAM_CACHE_DIR'] = str(work / 'cache')
    os.environ['LANSTREAM_PORT'] = '8061'
    (work / 'config.txt').write_text(f'port=8061\ndirectory_scan_timeout=300\n{root}\n', encoding='utf-8')
    import app as server
    server.app.config['TESTING'] = True
    client = server.app.test_client()
    if trace_memory:
        tracemalloc.start()
    cpu = time.process_time()
    enumeration, entries = measure(lambda: sum(1 for _ in os.scandir(root)), 1)
    start = time.perf_counter()
    response = client.get('/browse/0/')
    cold = (time.perf_counter() - start) * 1000
    report = {'entries': entries, 'enumeration': enumeration,
              'cold_html_ms': round(cold, 3), 'cold_status': response.status_code}
    if hasattr(server, 'directory_index'):
        index = server.directory_index
        start = time.perf_counter()
        client.get('/api/browse/0/')
        if not index.wait_for_idle(timeout=300):
            raise TimeoutError('Directory scan did not finish within 300 seconds')
        first_page = client.get('/api/browse/0/').get_json()
        if not first_page['version'] or first_page['state'] != 'ready':
            raise RuntimeError('Cold scan failed; refusing to report loading data as warm results')
        report['cold_first_page_ms'] = round((time.perf_counter() - start) * 1000, 3)
        warm, response = measure(lambda: client.get('/api/browse/0/'), repeats)
        report['warm_first_page_api'] = warm
        report['returned_items'] = len(response.get_json()['items'])
        searches, _ = measure(lambda: client.get('/api/search?q=000'), repeats)
        report['search'] = searches
        report['index_metrics'] = index.metrics()
    warm_html, response = measure(lambda: client.get('/browse/0/'), repeats)
    parser = ItemCounter()
    parser.feed(response.get_data(as_text=True))
    report.update(warm_html=warm_html, html_bytes=len(response.data),
                  rendered_items=parser.items, html_elements=parser.elements,
                  python_peak_mib=round(tracemalloc.get_traced_memory()[1] / 1048576, 2) if trace_memory else None,
                  cpu_seconds=round(time.process_time() - cpu, 3))
    if trace_memory:
        tracemalloc.stop()
    report['notes'] = ['Local filesystem/OS cache conditions; not N100/SMB acceptance.',
                       'HTML element count is server markup; browser DOM measured separately.',
                       'Latency measured with Python heap tracing ' + ('enabled.' if trace_memory else 'disabled.')]
    if hasattr(server, 'directory_index'):
        server.directory_index.close()
    if hasattr(server.image_scheduler, 'close'):
        server.image_scheduler.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, help='Explicit existing root; read-only, no media writes')
    parser.add_argument('--count', type=int, default=25969)
    parser.add_argument('--repeats', type=int, default=20)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--trace-memory', action='store_true', help='Adds substantial overhead; keep separate from latency runs')
    args = parser.parse_args()
    if args.count < 0 or args.repeats < 1:
        parser.error('count must be nonnegative and repeats positive')
    with tempfile.TemporaryDirectory(prefix='lanstream-benchmark-') as directory:
        work = Path(directory)
        root = args.root.absolute() if args.root else work / 'media'
        if not args.root:
            root.mkdir()
            for number in range(args.count):
                (root / f'Clip {number:06d}.mp4').touch()
        report = benchmark(root, work, args.repeats, args.trace_memory)
        output = json.dumps(report, indent=2)
        print(output)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
