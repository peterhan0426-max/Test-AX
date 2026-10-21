"""Strict readers for the HTML tables exported by ATPOS as .xls."""
import re
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser


def clean(value):
    return re.sub(r'\s+', '', value)


class Table(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], [], None
        self.colspan, self.rowspan = 1, 1
        self.pending = {}

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row = []
        if tag in ('td', 'th'):
            opts = dict(attrs)
            self.cell = ''
            self.colspan = int(opts.get('colspan', '1'))
            self.rowspan = int(opts.get('rowspan', '1'))
            if not 1 <= self.colspan <= 100 or not 1 <= self.rowspan <= 10:
                raise ValueError('지원하지 않는 셀 병합입니다.')
        if tag == 'br' and self.cell is not None:
            self.cell += ' '

    def handle_data(self, data):
        if self.cell is not None:
            self.cell += data

    def carry(self):
        while len(self.row) in self.pending:
            column = len(self.row)
            text, remaining = self.pending[column]
            self.row.append(text)
            if remaining == 1:
                del self.pending[column]
            else:
                self.pending[column] = text, remaining - 1

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            self.carry()
            for offset in range(self.colspan):
                value = self.cell.strip() if offset == 0 else ''
                if self.rowspan > 1:
                    self.pending[len(self.row)] = value, self.rowspan - 1
                self.row.append(value)
            self.cell = None
        if tag == 'tr' and self.row:
            self.carry()
            self.rows.append(self.row)


def amount(value):
    text = value.strip().replace(',', '').replace('\u2212', '-')
    if text.startswith('(') and text.endswith(')'):
        text = '-' + text[1:-1]
    if not text:
        raise ValueError('금액이 비어 있습니다. 0으로 바꾸지 않습니다.')
    try:
        result = Decimal(text)
    except InvalidOperation as error:
        raise ValueError('숫자로 읽을 수 없는 금액입니다.') from error
    if not result.is_finite() or result != result.to_integral_value():
        raise ValueError('원 단위 정수 금액이 필요합니다.')
    return int(result)


def promotion_type(name):
    if any(word in name for word in ('농할', '농축산물할인')):
        return '농할'
    if '수산' in name:
        return '수산대전'
    if any(word in name for word in ('임신', '임산부', '출산')):
        return '임산부'
    return '미분류'


def read_report(content):
    if content.startswith(bytes.fromhex('d0cf11e0')) or content.startswith(b'PK'):
        raise ValueError('이 수집기는 ATPOS HTML 형식 엑셀을 지원합니다.')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = content.decode('cp949')
    table = Table()
    table.feed(text)
    if len(table.rows) < 2:
        raise ValueError('보고서 표가 없습니다. 로그인 오류 화면인지 확인하세요.')
    first = [clean(v) for v in table.rows[0]]
    if '기초재고' in first and '순매입' in first:
        kind, headers, body = 'monthly-profit', first, table.rows[1:]
    elif '고객번호' in first and '프로모션명' in first:
        kind, headers, body = 'promotion-items', first, table.rows[1:]
    elif '회차별' in first:
        kind, headers, body = 'promotion-summary', None, table.rows[2:]
    elif '매출일자' in first and '총매출' in first:
        kind, headers, body = 'daily-sales', [clean(v) for v in table.rows[1]], table.rows[2:]
    else:
        raise ValueError('알 수 없는 보고서입니다.')
    output, totals = [], None
    checks = None
    for row in body:
        if not row:
            continue
        footer = clean(row[0]) in ('전체합계', '합계')
        if kind == 'promotion-summary':
            if len(row) != 8:
                raise ValueError('프로모션 요약 열 수가 다릅니다.')
            item = {'promotion': row[0], 'type': promotion_type(row[0]),
                    'gross': amount(row[1]), 'net': amount(row[2]),
                    'discount': amount(row[3]), 'difference': amount(row[4]),
                    'item_discount': amount(row[5])}
            if item['gross'] != item['net'] + item['discount']:
                raise ValueError('프로모션 요약의 총매출과 순매출·할인 합계가 다릅니다.')
            checks = ('gross', 'net', 'discount', 'item_discount', 'difference')
        else:
            if len(row) != len(headers):
                raise ValueError('보고서의 열 수가 헤더와 다릅니다.')
            record = dict(zip(headers, row))
            if kind == 'monthly-profit':
                if not footer and not row[0].isdigit():
                    continue
                item = {'product': record['상품코드'], 'name': record['상품명'],
                        'subcategory': record['소분류'], 'net': amount(record['순매출']),
                        'purchases': amount(record['순매입']), 'opening': amount(record['기초재고']),
                        'closing': amount(record['기말재고']), 'discount': amount(record['할인액'])}
                # 총할인 also includes adjustment/waste effects; 할인액 matches daily sales.
                checks = ('net', 'purchases', 'opening', 'closing', 'discount')
            elif kind == 'daily-sales':
                if not footer and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', row[0]):
                    continue
                item = {'date': row[0], 'gross': amount(record['총매출']),
                        'net': amount(record['순매출']), 'discount': amount(record['할인'])}
                if item['gross'] != item['net'] + item['discount']:
                    raise ValueError('일별 총매출과 순매출·할인 합계가 다릅니다.')
                checks = ('gross', 'net', 'discount')
            else:
                # Deliberately whitelist fields: no customer/card/receipt identifiers.
                item = {'promotion': record['프로모션명'], 'type': promotion_type(record['프로모션명']),
                        'date': record['매출일자'], 'store': record['매장명'],
                        'product': record['상품코드'], 'name': record['상품명'],
                        'gross': amount(record['총매출액']), 'net': amount(record['순매출액']),
                        'discount': amount(record['할인금액'])}
                if item['gross'] != item['net'] + item['discount']:
                    raise ValueError('프로모션 상세 금액이 일치하지 않습니다.')
                checks = ('gross', 'net', 'discount')
        if footer:
            if totals is not None:
                raise ValueError('전체 합계가 여러 개 있습니다.')
            totals = item
        else:
            output.append(item)
    if not output or totals is None:
        raise ValueError('상세 행과 전체 합계가 모두 필요합니다.')
    for key in checks:
        if sum(row[key] for row in output) != totals[key]:
            raise ValueError(f'행 합계가 보고서 전체 합계와 다릅니다: {key}')
    return {'kind': kind, 'rows': output, 'totals': {key: totals[key] for key in checks}}
