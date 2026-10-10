"""Start MMM locally and open the browser after the server is ready."""
import argparse
import errno
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser(description='启动 MMM 并打开浏览器；Ctrl+C 停止服务')
parser.add_argument('--port', type=int, default=None, help='指定端口；默认随机选择空闲端口')
parser.add_argument('--no-open', action='store_true', help='启动服务但不打开浏览器')
args = parser.parse_args()
if args.port is not None and not 1 <= args.port <= 65535:
    parser.error('端口必须介于 1 和 65535')
root = Path(__file__).resolve().parents[1]
url = f'http://127.0.0.1:{args.port}'
# Reserve the selected port until the child inherits the socket.
listener = socket.socket()

def ready():
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            return response.status == 200 and 'MMM' in response.read().decode('utf-8')
    except (OSError, UnicodeError):
        return False

def open_browser():
    if not args.no_open:
        subprocess.run(['/usr/bin/open', url], check=True)

try:
    listener.bind(('127.0.0.1', args.port or 0))
except OSError as error:
    listener.close()
    if error.errno != errno.EADDRINUSE or args.port is None:
        parser.exit(1, f"无法绑定本地端口：{error}\n")
    if ready():
        print(f'MMM 已运行：{url}', flush=True)
        open_browser()
        sys.exit(0)
    parser.exit(1, f'端口 {args.port} 已被其他服务占用，请使用 --port 指定其他端口。\n')
args.port = listener.getsockname()[1]
url = f'http://127.0.0.1:{args.port}'
listener.listen(128)
try:
    process = subprocess.Popen(
        [sys.executable, '-c',
         'import sys, uvicorn; uvicorn.run("calendar_agent.app:create_app", '
         'factory=True, fd=int(sys.argv[1]), access_log=False)',
         str(listener.fileno())],
        cwd=root, pass_fds=(listener.fileno(),))
finally:
    listener.close()
def stop(signum, frame):
    raise KeyboardInterrupt
signal.signal(signal.SIGTERM, stop)
try:
    deadline = time.monotonic() + 60
    while process.poll() is None and time.monotonic() < deadline:
        if ready():
            print(f'MMM 已启动：{url}（Ctrl+C 停止）', flush=True)
            open_browser()
            sys.exit(process.wait())
        time.sleep(0.25)
    if process.poll() is not None:
        sys.exit(process.returncode or 1)
    print('MMM 启动超时，请检查上方日志。', file=sys.stderr)
    sys.exit(1)
except KeyboardInterrupt:
    print('\n正在停止 MMM…', flush=True)
finally:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
