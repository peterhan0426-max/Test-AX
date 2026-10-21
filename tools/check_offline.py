"""Check the delivered encrypted ZIP in an actual offline browser."""
import argparse
import base64
import json
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright
from saenong.data import dashboard
from browser_origin import crypto_origin


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--password-file', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--executable')
    parser.add_argument('--screenshot', type=Path)
    args = parser.parse_args()
    password = args.password_file.read_text('utf-8').strip()
    expected = dashboard(args.data_dir)
    with zipfile.ZipFile(args.archive) as archive:
        assert archive.namelist() == ['새농.html']
        document = archive.read('새농.html').decode('utf-8')
    assert 'saenong-snapshot' not in document
    assert 'ATPOS_PASSWORD' not in document
    start = '<script id="sealed-data" type="application/json">'
    encoded = document.split(start, 1)[1].split('</script>', 1)[0]
    payload = json.loads(encoded)
    tampered = dict(payload)
    ciphertext = bytearray(base64.b64decode(payload['ciphertext']))
    ciphertext[len(ciphertext) // 2] ^= 1
    tampered['ciphertext'] = base64.b64encode(ciphertext).decode('ascii')
    with tempfile.TemporaryDirectory(dir=args.password_file.parent) as directory:
        root = Path(directory)
        file, broken = root / '새농.html', root / 'tampered.html'
        file.write_text(document, encoding='utf-8')
        broken.write_text(document.replace(encoded, json.dumps(tampered)), encoding='utf-8')
        with sync_playwright() as playwright:
            options = {'headless': True}
            if args.executable:
                options['executable_path'] = args.executable
            browser = playwright.chromium.launch(**options)
            try:
                context = browser.new_context(viewport={'width': 1440, 'height': 1000}, offline=True)
                page = context.new_page()
                errors, network = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: network.append(request.url)
                        if request.url.startswith(('http:', 'https:')) else None)
                good_url, broken_url = file.resolve().as_uri(), broken.resolve().as_uri()
                direct_file = True
                try:
                    page.goto(broken_url)
                except Exception as error:
                    if 'ERR_BLOCKED_BY_ADMINISTRATOR' not in str(error):
                        raise
                    # Respect the cloud browser's file-URL policy. Exercise the
                    # identical document as a Blob, with the origin server
                    # stopped before any dashboard interaction.
                    direct_file = False
                    with crypto_origin() as origin:
                        context.set_offline(False)
                        controller = context.new_page()
                        controller.goto(origin)
                        good_url, broken_url = controller.evaluate(
                            "documents=>documents.map(text=>URL.createObjectURL(new Blob([text],{type:'text/html'})))",
                            [document, broken.read_text('utf-8')])
                        context.set_offline(True)
                    page.goto(broken_url)
                page.locator('#password').fill(password)
                page.locator('#open').click()
                page.wait_for_function("document.getElementById('message').textContent.includes('손상')")
                assert page.locator('#cards').count() == 0
                page.goto(good_url)
                page.locator('#password').fill('incorrect-password')
                page.locator('#open').click()
                page.wait_for_function("document.getElementById('message').textContent.includes('암호가 맞지')")
                assert page.locator('#cards').count() == 0
                page.locator('#password').fill(password)
                page.locator('#open').click()
                page.wait_for_function("document.querySelectorAll('#cards .card').length===3")
                assert page.locator('#period option').count() == len(expected['periods'])
                for view in expected['stores']:
                    if not view.get('missing'):
                        assert f"{view['gross']:,}원" in page.locator('#cards').inner_text()
                assert '자동으로 갱신되지 않습니다' in page.locator('#saved-note').inner_text()
                if expected['warnings']:
                    assert not page.locator('#warning-details').evaluate('(node)=>node.open')
                    page.locator('#warning-summary').click()
                    assert page.locator('#warnings .warning:visible').count() == len(expected['warnings'])
                    page.locator('#warning-summary').click()
                past = expected['periods'][-1]
                page.locator('#period').select_option(past)
                page.wait_for_function('period=>currentData?.period===period', arg=past)
                assert page.evaluate('currentData.stores') == dashboard(args.data_dir, past)['stores']
                page.locator('#period').select_option(expected['period'])
                page.wait_for_function('period=>currentData?.period===period', arg=expected['period'])
                categories = page.locator('#trend-category option')
                if categories.count() > 1:
                    page.locator('#trend-category').select_option(index=1)
                    assert '매출 추이' in page.locator('#trend-title').inner_text()
                    page.locator('#trend-category').select_option('')
                if args.screenshot:
                    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(args.screenshot), full_page=True)
                page.set_viewport_size({'width': 390, 'height': 900})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert not network, 'The file must not depend on an HTTP server'
                assert not errors, errors
                print('PASS: standalone offline document, correct/wrong password, tamper rejection, all months, values, charts and mobile layout')
                print('File URL check: PASS' if direct_file else 'File URL check: BLOCKED by cloud browser policy; identical document tested offline as a Blob')
            finally:
                browser.close()


if __name__ == '__main__':
    main()
