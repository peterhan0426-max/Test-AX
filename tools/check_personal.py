"""Reproducible offline-viewer check with synthetic sales and no credentials."""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from saenong.data import import_report
from saenong.offline import render, snapshot
from package_viewer import package


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--executable')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'categories.json').write_text(json.dumps({'테스트': '기타'}), encoding='utf-8')
        headers = ['No', '소분류', '상품코드', '상품명', '순매출', '순매입', '기초재고', '기말재고', '할인액']
        for period, factor in [('2025-09', 1), ('2025-10', 2), ('2026-10', 3)]:
            for index, store in enumerate(('가락', '야탑', '동천'), 1):
                values = [str(1000000 * factor * index), '600000', '200000', '150000', '100000']
                cells = lambda row: ''.join('<td>' + value + '</td>' for value in row)
                document = ('<table><tr>' + cells(headers) + '</tr><tr>' +
                            cells(['1', '테스트', 'P1', '테스트 상품'] + values) +
                            '</tr><tr><td colspan="4">전체 합계</td>' + cells(values) + '</tr></table>')
                source = root / 'source.xls'
                source.write_bytes(document.encode('cp949'))
                cutoff = period + ('-30' if period.endswith('09') else '-31')
                import_report(root, source, store, period, cutoff, source_query={'check_yn': '5'})
        file, archive, password = root / 'viewer.html', root / 'viewer.zip', root / 'password.txt'
        file.write_text(render(snapshot(root)), encoding='utf-8')
        package(file, archive, password, args.executable)
        command = [sys.executable, str(Path(__file__).with_name('check_offline.py')),
                   '--archive', str(archive), '--password-file', str(password), '--data-dir', str(root)]
        if args.executable:
            command.extend(['--executable', args.executable])
        subprocess.run(command, check=True)


if __name__ == '__main__':
    main()
