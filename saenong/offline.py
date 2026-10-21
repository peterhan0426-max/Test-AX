"""Export the existing read-only dashboard as a self-contained HTML file."""
import argparse
import json
from datetime import datetime
from pathlib import Path

from .data import KST, dashboard


def snapshot(root):
    current = dashboard(root)
    views = {}
    for period in current['periods'] or [current['period']]:
        view = current if period == current['period'] else dashboard(root, period)
        # Job diagnostics and filenames are not needed for personal viewing.
        collection = view.get('collection')
        view['collection'] = {'finished_at': collection.get('finished_at')} if collection else None
        views[period] = view
    return {'version': 1, 'created_at': datetime.now(KST).isoformat(),
            'initial_period': current['period'], 'views': views}


def render(bundle):
    content = json.dumps(bundle, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    # A report label must not terminate the JSON script element.
    content = content.replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    template = Path(__file__).with_name('index.html').read_text('utf-8')
    marker = '<!-- snapshot-data -->'
    if template.count(marker) != 1:
        raise ValueError('조회 화면에 자료를 삽입할 위치를 확인할 수 없습니다.')
    return template.replace(marker, '<script id="saenong-snapshot" type="application/json">' + content + '</script>')


def main():
    parser = argparse.ArgumentParser(description='설치 없이 여는 새농 개인용 HTML 저장본')
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    checkout = Path(__file__).resolve().parents[1]
    if args.output.resolve().is_relative_to(checkout):
        parser.error('실제 매출이 담긴 HTML은 코드 저장소 밖의 비공개 폴더에 저장하세요.')
    bundle = snapshot(args.data_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(bundle), encoding='utf-8')
    print(f'개인용 HTML 저장 완료: {len(bundle["views"])}개월. 자동 갱신되지 않는 저장본입니다.')


if __name__ == '__main__':
    main()
