"""Interactive, local-only ATPOS download capture. Never stores passwords in code."""
import argparse
import hashlib
import json
import os
import sqlite3
import uuid
import time
from datetime import datetime
from pathlib import Path

REPORTS = ('monthly-profit', 'promotion-summary', 'promotion-items', 'daily-sales')


def archive(source: Path, root: Path, metadata: dict):
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    raw = root / 'private-originals'
    raw.mkdir(parents=True, exist_ok=True)
    target = raw / (digest + '.xls')
    # Content-addressed files retain the exact original without trusting its name.
    if not target.exists():
        with target.open('xb') as stream:
            stream.write(content)
    with sqlite3.connect(root / 'captures.sqlite3') as db:
        db.execute('''CREATE TABLE IF NOT EXISTS captures (
            id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, metadata TEXT NOT NULL,
            captured_at TEXT NOT NULL)''')
        db.execute('INSERT INTO captures VALUES (?, ?, ?, ?)',
                   (str(uuid.uuid4()), digest, json.dumps(metadata, ensure_ascii=False),
                    datetime.now().astimezone().isoformat()))
    return digest


def main():
    parser = argparse.ArgumentParser(description='aTPOS 로그인 및 다운로드 보관')
    parser.add_argument('--data-dir', type=Path,
                        default=Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'SaenongCollector')
    args = parser.parse_args()
    root = args.data_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    print('로그인 세션과 개인정보가 포함된 원본의 비공개 저장 위치:', root)
    print('화면에서 직접 로그인하세요. 비밀번호는 프로그램에 입력하지 않습니다.')
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(root / 'browser-profile'), headless=False, accept_downloads=True)
        try:
            downloads = []
            def watch(tab):
                tab.on('download', lambda item: downloads.append(item))
            context.on('page', watch)
            for tab in context.pages:
                watch(tab)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto('https://www2.atpos.co.kr/Login/', wait_until='domcontentloaded')
            print('로그인 후 보고서를 조회하세요. 다운로드마다 조건을 등록합니다.')
            while True:
                kind = input('종류: 1 월별손익 / 2 프로모션요약 / 3 프로모션상세 / 4 일별매출 / q 종료: ').strip()
                if kind.lower() == 'q':
                    break
                if kind not in ('1', '2', '3', '4'):
                    continue
                store = input('조회 화면의 매장명 (전체 조회면 전체): ').strip()
                start = input('조회 시작일 YYYY-MM-DD: ').strip()
                end = input('조회 종료일 YYYY-MM-DD: ').strip()
                try:
                    first = datetime.strptime(start, '%Y-%m-%d').date()
                    last = datetime.strptime(end, '%Y-%m-%d').date()
                    if not store or first > last:
                        raise ValueError()
                except ValueError:
                    print('매장명과 날짜를 확인하세요.')
                    continue
                print('지금 브라우저에서 엑셀 버튼을 누르세요. 5분간 기다립니다.')
                temporary = root / ('pending-' + uuid.uuid4().hex + '.xls')
                try:
                    downloads.clear()
                    deadline = time.monotonic() + 300
                    while not downloads:
                        if time.monotonic() >= deadline:
                            raise TimeoutError('Download timed out')
                        page.wait_for_timeout(100)
                    download = downloads.pop(0)
                    download.save_as(temporary)
                    if download.failure():
                        raise RuntimeError(download.failure())
                    digest = archive(temporary, root, {
                        'report': REPORTS[int(kind) - 1], 'store': store,
                        'requested_start': start, 'requested_end': end,
                        'original_filename': download.suggested_filename,
                        'conditions_verified': False,
                    })
                    print('원본 보관 완료:', digest[:12], '(조회 조건·내용 검증 전)')
                    # Conditions for monthly reports are entered once for the downloaded report.
                    # Never combine multiple months or stores into one financial snapshot.
                    if first.strftime('%Y-%m') == last.strftime('%Y-%m') and store in ('가락', '야탑', '동천'):
                        from saenong.data import import_report
                        try:
                            result = import_report(root, temporary, store, first.strftime('%Y-%m'), end)
                            if result['kind'] != REPORTS[int(kind) - 1]:
                                print('안내: 선택한 종류와 파일 종류가 달라 파일의 실제 종류로 반영했습니다.')
                            print('집계 검증 및 대시보드 반영 완료:', result['rows'], '행')
                        except ValueError as error:
                            print('원본만 보관했습니다. 집계 미반영:', str(error))
                    else:
                        print('원본만 보관했습니다. 집계는 단일 매장·단일 월 조회를 사용하세요.')
                except Exception as error:
                    print('보관 실패:', type(error).__name__, '다시 시도하세요.')
                finally:
                    temporary.unlink(missing_ok=True)
        finally:
            context.close()


if __name__ == '__main__':
    main()
