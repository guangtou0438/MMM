"""Render a synthetic PDF in an OS temp directory and retain only the preview."""
import sys
import tempfile
import subprocess
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from calendar_agent.tools.export import render

items = [{'title': '合成用户的户外跑步计划', 'activity_type': 'arrangement', 'planned_date': '2026-09-07',
          'deadline': '', 'checklist': '准备运动鞋\n检查天气', 'status': 'open'},
         {'title': '已完成的阅读任务', 'activity_type': 'task', 'planned_date': '2026-09-06',
          'deadline': '2026-09-12', 'checklist': '阅读第一章\n写下三个问题', 'status': 'completed'}]
poppler = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe'
with tempfile.TemporaryDirectory(prefix='mmm-pdf-') as directory:
    path = Path(directory)/'synthetic.pdf'
    path.write_bytes(render(items, 'pdf', datetime.now(timezone.utc))[0])
    subprocess.run([str(poppler), '-png', '-scale-to', '1200', '-singlefile', str(path), 'artifacts/refactor-export-preview'], check=True)
print('Synthetic PDF rendered; source PDF removed with temporary directory.')
