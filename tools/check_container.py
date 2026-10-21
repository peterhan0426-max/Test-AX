"""Verify container auth, backup, crash recovery and persistence with synthetic data."""
import argparse
import base64
import json
import os
import subprocess
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default='saenong-runtime:local')
    args = parser.parse_args()
    name = 'saenong-check-' + uuid.uuid4().hex[:10]
    volume = name + '-data'

    def docker(*arguments, check=True):
        return subprocess.run(['docker', *arguments], capture_output=True, text=True,
                              encoding='utf-8', check=check)

    password = 'synthetic-viewer-password'
    authorization = 'Basic ' + base64.b64encode(('saenong:' + password).encode()).decode()

    def get(base, route, authenticated=False):
        headers = {'Authorization': authorization} if authenticated else {}
        with urlopen(Request(base + route, headers=headers), timeout=3) as response:
            return json.load(response)

    try:
        missing = docker('run', '--rm', args.image, check=False)
        assert missing.returncode != 0, 'Production must refuse to run without private-access settings'
        docker('volume', 'create', volume)
        docker('run', '-d', '--name', name, '--restart', 'unless-stopped',
               '-e', 'SAENONG_AUTOCOLLECT=0', '-e', 'SAENONG_VIEWER_PASSWORD=' + password,
               '-v', volume + ':/var/saenong', '-p', '127.0.0.1::8000', args.image)
        port = docker('port', name, '8000/tcp').stdout.strip().rsplit(':', 1)[1]
        base = 'http://127.0.0.1:' + port
        for _ in range(60):
            try:
                if get(base, '/health') == {'status': 'ok'}:
                    break
            except OSError:
                time.sleep(0.5)
        else:
            raise AssertionError('Container did not become healthy')
        try:
            get(base, '/api/dashboard')
            raise AssertionError('Unauthenticated financial data was exposed')
        except HTTPError as error:
            assert error.code == 401
        seed = '''from pathlib import Path
from saenong.data import import_report
root=Path('/var/saenong/data')
headers=['No','소분류','상품코드','상품명','순매출','순매입','기초재고','기말재고','할인액']
values=['100','50','20','10','10']
cells=lambda row: ''.join('<td>'+value+'</td>' for value in row)
html='<table><tr>'+cells(headers)+'</tr><tr>'+cells(['1','테스트','P1','테스트 상품']+values)+'</tr><tr><td colspan="4">전체 합계</td>'+cells(values)+'</tr></table>'
file=root/'source.xls';file.write_bytes(html.encode('cp949'))
import_report(root,file,'가락','2026-10','2026-10-21',source_query={'check_yn':'5'})
'''
        docker('exec', '--user', '10001', name, 'python', '-c', seed)
        assert get(base, '/api/dashboard', True)['stores'][0]['gross'] == 110
        docker('exec', '--user', '10001', name, 'python', '-m', 'saenong.backup',
               '--data-dir', '/var/saenong/data', '--backup-dir', '/var/saenong/backups')
        killer = '''from pathlib import Path
import os,signal
for process in Path('/proc').iterdir():
    if process.name.isdigit():
        try: parts=(process/'cmdline').read_bytes().split(b'\\0')
        except (OSError,ProcessLookupError): continue
        if parts[1:4]==[b'-u',b'-m',b'saenong.backup']:
            os.kill(int(process.name),signal.SIGKILL)
            break
else: raise SystemExit('Backup worker not found')
'''
        docker('exec', name, 'python', '-c', killer)
        for _ in range(90):
            restarts = int(docker('inspect', '--format', '{{.RestartCount}}', name).stdout)
            binding = docker('port', name, '8000/tcp', check=False).stdout.strip()
            if binding:
                base = 'http://127.0.0.1:' + binding.rsplit(':', 1)[1]
            try:
                if restarts > 0 and get(base, '/api/dashboard', True)['stores'][0]['gross'] == 110:
                    break
            except OSError:
                pass
            time.sleep(0.5)
        else:
            raise AssertionError('Crash restart did not preserve the imported data')
        print('PASS: container authentication, consistent backup, crash restart and persistent data')
        print('Collector disabled for this synthetic container check; no ATPOS requests were made.')
    except Exception:
        diagnostic = docker('logs', '--tail', '60', name, check=False)
        print(diagnostic.stdout + diagnostic.stderr)
        raise
    finally:
        docker('rm', '-f', name, check=False)
        docker('volume', 'rm', volume, check=False)


if __name__ == '__main__':
    main()
