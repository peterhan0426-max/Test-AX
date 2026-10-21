"""Establish a private browser session using configured credential bindings."""
import argparse
import os
import re
from pathlib import Path


def authenticate_page(page, account, password):
    page.locator('input[name="ms_id"]').fill(account)
    page.locator('input[name="passwd"]').fill(password)
    page.locator('button[name="submit_img"]').click()
    # The site may navigate into a frameset. Require signed-in UI, not just a URL change.
    for _ in range(100):
        for frame in page.frames:
            if frame.get_by_text(re.compile(r'^로그\s*아웃$')).count():
                return True
        page.wait_for_timeout(200)
    return False


def main():
    parser = argparse.ArgumentParser(description='aTPOS 인증 연결 확인')
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--executable', help='설치된 Chromium 실행 파일 경로')
    args = parser.parse_args()
    account, password = os.environ.get('ATPOS_ID'), os.environ.get('ATPOS_PASSWORD')
    if not account or not password:
        parser.exit(2, '환경 설정의 ATPOS_ID 및 ATPOS_PASSWORD 인증 연결이 필요합니다. 값을 채팅에 보내지 마세요.\n')
    from playwright.sync_api import sync_playwright
    args.data_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        options = {'headless': True, 'accept_downloads': True}
        if args.executable:
            options['executable_path'] = args.executable
        context = playwright.chromium.launch_persistent_context(
            str(args.data_dir / 'atpos-profile'), **options)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            # Never log request URLs: this legacy site's JavaScript uses credential query parameters.
            page.on('dialog', lambda dialog: dialog.dismiss())
            page.goto('https://www2.atpos.co.kr/Login/', wait_until='domcontentloaded')
            if not authenticate_page(page, account, password):
                parser.exit(1, '로그인 완료를 확인하지 못했습니다. 계정·권한 또는 추가 인증 확인이 필요합니다.\n')
            print('로그인 완료 UI 확인. 비공개 브라우저 세션을 저장했습니다.')
        except Exception:
            # Playwright errors can include the site's credential-bearing URL. Do not print them.
            parser.exit(1, '인증 연결을 완료하지 못했습니다. 네트워크·인증서·로그인 화면을 확인해야 합니다.\n')
        finally:
            context.close()


if __name__ == '__main__':
    main()
