"""Make a password-protected single-file viewer using browser Web Crypto.

Only the encrypted HTML is placed in the downloadable archive. The source
HTML, original reports and unlock password stay outside the Git checkout.
"""
import argparse
import json
import os
import secrets
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright
from browser_origin import crypto_origin


def package(source, output, password_file, executable=None):
    checkout = Path(__file__).resolve().parents[1]
    source, output, password_file = map(Path, (source, output, password_file))
    for private in (source, password_file):
        if private.resolve().is_relative_to(checkout):
            raise ValueError('원본 HTML과 열기 암호는 코드 저장소 밖에 보관해야 합니다.')
    if password_file.exists():
        password = password_file.read_text('utf-8').strip()
    else:
        password_file.parent.mkdir(parents=True, exist_ok=True)
        password = secrets.token_urlsafe(16)
        descriptor = os.open(password_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(password + '\n')
    if len(password) < 20:
        raise ValueError('열기 암호는 20자 이상이어야 합니다.')
    with crypto_origin() as origin:
        with sync_playwright() as playwright:
            options = {'headless': True}
            if executable:
                options['executable_path'] = executable
            browser = playwright.chromium.launch(**options)
            try:
                page = browser.new_page()
                page.goto(origin)
                sealed = page.evaluate('''async ({html,password}) => {
                    function base64(input) {const bytes=new Uint8Array(input),parts=[];
                      for(let i=0;i<bytes.length;i+=32768)parts.push(String.fromCharCode(...bytes.subarray(i,i+32768)));
                      return btoa(parts.join(''));}
                    const salt=crypto.getRandomValues(new Uint8Array(16)),iv=crypto.getRandomValues(new Uint8Array(12)),iterations=310000;
                    const base=await crypto.subtle.importKey('raw',new TextEncoder().encode(password),'PBKDF2',false,['deriveKey']);
                    const key=await crypto.subtle.deriveKey({name:'PBKDF2',salt,iterations,hash:'SHA-256'},base,{name:'AES-GCM',length:256},false,['encrypt']);
                    const ciphertext=await crypto.subtle.encrypt({name:'AES-GCM',iv},key,new TextEncoder().encode(html));
                    return {version:1,salt:base64(salt),iv:base64(iv),iterations,ciphertext:base64(ciphertext)};
                }''', {'html': source.read_text('utf-8'), 'password': password})
            finally:
                browser.close()
    template = (checkout / 'saenong' / 'locked.html').read_text('utf-8')
    protected = template.replace('SEALED_PAYLOAD', json.dumps(sealed, separators=(',', ':')))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.tmp')
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('새농.html', protected.encode('utf-8'))
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def main():
    parser = argparse.ArgumentParser(description='개인용 HTML을 암호화해 전달용 ZIP으로 저장')
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--password-file', type=Path, required=True)
    parser.add_argument('--executable')
    args = parser.parse_args()
    package(args.source, args.output, args.password_file, args.executable)
    print('암호화된 개인용 파일 생성 완료. 열기 암호와 원본은 포함하지 않았습니다.')


if __name__ == '__main__':
    main()
