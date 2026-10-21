"""Read-only ATPOS report collection through the site's verified form endpoints."""
import http.cookiejar
import hashlib
import json
import re
import time
import ssl
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from .authenticate import ORIGIN, SafeRedirect, establish_session
from saenong.data import KST, import_report, month_end
from saenong.reports import read_report

STORE_CODES = {'가락': 'NC3865', '동천': 'NC3866', '야탑': 'NC4230'}
PATHS = {'monthly-profit': '/OP/sale/analysis/union/sanu910.php3',
         'promotion-summary': '/OP/sale/promotion/chain/sprc030.php3',
         'promotion-items': '/OP/sale/promotion/chain/sprc020.php3',
         'daily-sales': '/OP/sale/stage/union/sstu030.php3'}


def read_limited(response, seconds=120):
    deadline = time.monotonic() + seconds
    chunks, size = [], 0
    while True:
        chunk = response.read1(65536)
        if time.monotonic() > deadline:
            raise TimeoutError('보고서 전체 응답 제한 시간을 초과했습니다.')
        if not chunk:
            return b''.join(chunks)
        size += len(chunk)
        if size > 128 * 1024 * 1024:
            raise ValueError('보고서가 수집 크기 제한을 초과했습니다.')
        chunks.append(chunk)


def export_data(text):
    match = re.search(r'<textarea\b[^>]*\bname\s*=\s*["\x27]?xls\b[^>]*>(.*?)</textarea\s*>', text, re.I | re.S)
    if not match:
        raise RuntimeError('보고서 내보내기 영역이 없습니다. 세션 또는 조회 권한을 확인해야 합니다.')
    return match[1].encode('cp949')


def fields_for(kind, store, period, cutoff, category=''):
    first = period + '-01'
    code = STORE_CODES[store]
    if kind == 'monthly-profit':
        year, month = period.split('-')
        return {'search_fg': 'Y', 'year1': year, 'month1': month, 'M_MS_NO': code,
                'LCLASS_CD': category, 'MCLASS_CD': '', 'SCLASS_CD': '', 'pGood_nm': '', 'check_yn': '5'}
    if kind in ('promotion-summary', 'promotion-items'):
        fields = {'load_fg': '1', 'year1': first.replace('-', '/'),
                  'year2': cutoff.replace('-', '/'), 'MS_NO': code}
        if kind == 'promotion-items':
            fields.update(pmYear='', pmCd='', pmNm='')
        return fields
    if kind == 'daily-sales':
        return {'year1': first.replace('-', '/'), 'year2': cutoff.replace('-', '/'),
                'MS_NO': code, 'POS_NO': '%', 'load_fg': 'Y'}
    raise ValueError('보고서 조회 조건이 아직 검증되지 않았습니다.')


class Client:
    def __init__(self, root, refresh=False):
        self.root = Path(root)
        path = self.root / 'atpos-session.json'
        state = establish_session(self.root) if refresh or not path.exists() else json.loads(path.read_text())
        self.jar = http.cookiejar.CookieJar()
        for item in state['cookies']:
            expires = item.get('expires')
            if expires is not None and expires <= 0:
                expires = None
            self.jar.set_cookie(http.cookiejar.Cookie(
                0, item['name'], item['value'], None, False, item['domain'], True,
                item['domain'].startswith('.'), item.get('path', '/'), True,
                item.get('secure', False), expires, expires is None,
                None, None, {}, False))
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar),
                                                 urllib.request.HTTPSHandler(context=ssl.create_default_context()), SafeRedirect())

    def report(self, kind, store, period, cutoff, category=''):
        fields = fields_for(kind, store, period, cutoff, category)
        request = urllib.request.Request(ORIGIN + PATHS[kind],
                                         data=urllib.parse.urlencode(fields, encoding='cp949').encode(),
                                         headers={'Referer': ORIGIN + PATHS[kind]}, method='POST')
        budget = 300 if kind == 'monthly-profit' and period == datetime.now(KST).strftime('%Y-%m') else 120
        with self.opener.open(request, timeout=120) as response:
            text = read_limited(response, budget).decode('cp949')
        content = export_data(text)
        original = self.root / 'private-originals' / (hashlib.sha256(content).hexdigest() + '.xls')
        original.parent.mkdir(parents=True, exist_ok=True)
        if not original.exists():
            with original.open('xb') as stream:
                stream.write(content)
        self.last_original_sha256 = original.stem
        report = read_report(content)
        if report['kind'] != kind:
            raise RuntimeError('요청한 보고서와 다른 파일이 반환됐습니다.')
        return content, report

    def collect(self, kind, store, period, cutoff):
        content, report = self.report(kind, store, period, cutoff)
        folder = self.root / 'downloads'
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(KST).strftime('%Y%m%dT%H%M%S%f')
        file = folder / f'{kind}-{STORE_CODES[store]}-{period}-{stamp}.xls'
        file.write_bytes(content)
        try:
            result = import_report(self.root, file, store, period, cutoff,
                                   source_query=fields_for(kind, store, period, cutoff),
                                   collected_at=datetime.now(KST).isoformat())
        finally:
            file.unlink(missing_ok=True)
        result.update(store=store, period=period, as_of=cutoff)
        return result
