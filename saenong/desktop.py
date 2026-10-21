"""Start the local read-only dashboard without exposing raw folders."""
import os
import threading
import webbrowser
from pathlib import Path

from .server import create_server


def main():
    root = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'SaenongCollector'
    server = create_server(root, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    webbrowser.open(f'http://127.0.0.1:{server.server_port}/')
    print('조회 화면을 열었습니다. 이 창을 닫으면 조회 서버가 종료됩니다.')
    try:
        input('서버를 종료하려면 Enter를 누르세요.\n')
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == '__main__':
    main()
