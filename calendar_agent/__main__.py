import argparse
import sys
import uvicorn
import socket

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

parser = argparse.ArgumentParser(description="MMM 个人记忆与日期待办 · P4")
parser.add_argument("--port", type=int, default=8765)
args = parser.parse_args()
if not 1 <= args.port <= 65535:
    parser.error("端口必须介于 1 和 65535")
with socket.socket() as probe:
    try: probe.bind(('127.0.0.1', args.port))
    except OSError:
        parser.exit(1, f'端口 {args.port} 已被占用。如果 MMM 已运行，请打开 http://127.0.0.1:{args.port}；切换版本时先停止旧服务。\n')
uvicorn.run("calendar_agent.app:create_app", factory=True, host="127.0.0.1", port=args.port, access_log=False)
