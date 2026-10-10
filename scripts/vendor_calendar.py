"""Fetch pinned public browser assets; runtime never depends on a CDN."""
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

root = Path(__file__).resolve().parent.parent / "calendar_agent/static/vendor"
root.mkdir(parents=True, exist_ok=True)
sources = {
    "fullcalendar.min.js": "https://cdn.jsdelivr.net/npm/fullcalendar@6.1.21/index.global.min.js",
    "luxon.min.js": "https://cdn.jsdelivr.net/npm/luxon@3.7.2/build/global/luxon.min.js",
    "fullcalendar-luxon.min.js": "https://cdn.jsdelivr.net/npm/@fullcalendar/luxon3@6.1.21/index.global.min.js",
    "zh-cn.min.js": "https://cdn.jsdelivr.net/npm/@fullcalendar/core@6.1.21/locales/zh-cn.global.min.js",
    "FULLCALENDAR-LICENSE.txt": "https://cdn.jsdelivr.net/npm/fullcalendar@6.1.21/LICENSE.md",
    "LUXON-LICENSE.txt": "https://cdn.jsdelivr.net/npm/luxon@3.7.2/LICENSE.md",
}
manifest = {}
for name, url in sources.items():
    with urlopen(url, timeout=30) as response:
        content = response.read()
    if len(content) < 100:
        raise RuntimeError(f"Unexpected asset: {name}")
    (root / name).write_bytes(content)
    manifest[name] = {"url": url, "sha256": hashlib.sha256(content).hexdigest()}
    print(name, len(content))
(root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
