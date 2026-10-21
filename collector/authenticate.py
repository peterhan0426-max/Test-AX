"""HTTPS POST authentication; credentials never appear in request URLs or logs."""
import argparse
import http.cookiejar
import json
import os
import re
import ssl
import urllib.parse
import urllib.request
from pathlib import Path

ORIGIN = 'https://www2.atpos.co.kr'
LOGIN = ORIGIN + '/Login/'
ENTRY = ORIGIN + '/Login/frame_set.php3'


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urllib.parse.urlsplit(newurl)
        keys = {name.lower() for name, _ in urllib.parse.parse_qsl(parts.query)}
        if parts.scheme != 'https' or parts.hostname != 'www2.atpos.co.kr' or keys & {'passwd', 'password'}:
            raise RuntimeError('안전하지 않은 인증 이동을 중단했습니다.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def login_request(account, password, stage):
    if stage not in ('user', 'pass'):
        raise ValueError('Invalid login stage')
    body = urllib.parse.urlencode({'ms_id': account, 'passwd': password,
                                  'pos_fg': '', 'proc_fg': stage}).encode()
    return urllib.request.Request(LOGIN + 'CheckUser.php3', data=body, method='POST',
                                  headers={'Content-Type': 'application/x-www-form-urlencoded', 'Referer': LOGIN})


def establish_session(root):
    account, password = os.environ.get('ATPOS_ID'), os.environ.get('ATPOS_PASSWORD')
    if not account or not password:
        raise RuntimeError('환경 설정의 ATPOS_ID 및 ATPOS_PASSWORD 연결이 필요합니다.')
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar),
                                        urllib.request.HTTPSHandler(context=ssl.create_default_context()), SafeRedirect())
    with opener.open(LOGIN, timeout=30) as response:
        response.read()
    for stage in ('user', 'pass'):
        with opener.open(login_request(account, password, stage), timeout=30) as response:
            text = response.read().decode('cp949', errors='replace')
        if stage == 'user' and text.strip() != 'true':
            raise RuntimeError('계정 확인을 완료하지 못했습니다.')
        if stage == 'pass' and '/Login/frame_set.php3' not in text:
            raise RuntimeError('인증 완료 응답을 확인하지 못했습니다.')
    cookies = []
    for cookie in jar:
        item = {'name': cookie.name, 'value': cookie.value, 'domain': cookie.domain,
                'path': cookie.path, 'secure': cookie.secure, 'httpOnly': 'HttpOnly' in cookie._rest}
        if cookie.expires:
            item['expires'] = cookie.expires
        cookies.append(item)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    state = {'cookies': cookies, 'origins': []}
    (root / 'atpos-session.json').write_text(json.dumps(state), encoding='utf-8')
    return state


def signed_in(page):
    return any(frame.get_by_text(re.compile(r'^로그\s*아웃$')).count() for frame in page.frames)


def main():
    parser = argparse.ArgumentParser(description='aTPOS 인증 연결 확인')
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--executable', help='설치된 Chromium 실행 파일 경로')
    args = parser.parse_args()
    try:
        state = establish_session(args.data_dir)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            options = {'headless': True}
            if args.executable:
                options['executable_path'] = args.executable
            browser = playwright.chromium.launch(**options)
            try:
                context = browser.new_context(storage_state=state)
                page = context.new_page()
                page.on('dialog', lambda dialog: dialog.dismiss())
                page.goto(ENTRY, wait_until='domcontentloaded')
                for _ in range(50):
                    if signed_in(page):
                        context.storage_state(path=str(args.data_dir / 'atpos-session.json'))
                        print('로그인 완료 UI 확인. 비공개 세션을 저장했습니다.')
                        return
                    page.wait_for_timeout(200)
                parser.exit(1, '인증 후 로그인 완료 화면을 확인하지 못했습니다.\n')
            finally:
                browser.close()
    except Exception:
        # Never expose errors that might contain credential-bearing request details.
        parser.exit(1, '인증 연결에 실패했습니다. 설정·네트워크·인증서 상태를 확인해야 합니다.\n')


if __name__ == '__main__':
    main()
