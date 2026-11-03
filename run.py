"""直接运行本文件；优先使用项目自己的虚拟环境。"""
from pathlib import Path
import argparse
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import json

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / '.venv' / 'Scripts' / 'python.exe'
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--port', type=int, default=8601)
    parser.add_argument('--open', action='store_true', help='服务就绪后打开浏览器')
    args = parser.parse_args()
    python = str(PYTHON) if PYTHON.exists() else sys.executable
    if args.check:
        raise SystemExit(subprocess.call([python, '-c',
            'from backend.main import app; print("Xuanwen ready: " + app.title)'], cwd=ROOT))
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/api/status', timeout=1) as response:
            if json.load(response).get('name') == '铉问':
                print(f'Xuanwen already running: http://127.0.0.1:{args.port}')
                if args.open:
                    webbrowser.open(f'http://127.0.0.1:{args.port}')
                raise SystemExit(0)
    except (OSError, ValueError):
        pass
    print(f'Xuanwen: http://127.0.0.1:{args.port}', flush=True)
    if args.open:
        def open_when_ready():
            url = f'http://127.0.0.1:{args.port}'
            for _ in range(40):
                try:
                    urllib.request.urlopen(url + '/api/status', timeout=1).close()
                    webbrowser.open(url)
                    return
                except Exception:
                    time.sleep(0.5)
        threading.Thread(target=open_when_ready, daemon=True).start()
    raise SystemExit(subprocess.call([python, '-m', 'uvicorn', 'backend.main:app',
        '--host', '127.0.0.1', '--port', str(args.port), '--no-access-log'], cwd=ROOT))
