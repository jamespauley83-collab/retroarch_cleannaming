#!/usr/bin/env python3
"""Lightweight HTTP server that serves a status page for the RetroArch CleanNaming CLI tool.

This exists so the Base44 preview (port 3000) has something to show — the actual
tool is a CLI script (rename_roms.py), not a web application.
"""
import os
import json
import subprocess
import http.server
import socketserver
from html import escape

PORT = 3000
CONFIG_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "config.json")
DB_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "libretro-database")
# These repository documents share the Mega Drive ROM extension.
PROJECT_MARKDOWN_FILES = {"README.md", "AGENTS.md"}


def build_status_page():
    # Check environment readiness
    db_ready = os.path.isdir(os.path.join(DB_PATH, ".git"))
    try:
        import Levenshtein  # noqa: F401
        deps_ready = True
    except ImportError:
        deps_ready = False

    # Load config extensions
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        extensions = ", ".join(sorted(config.keys()))
    except Exception:
        extensions = "(unable to read config.json)"

    rom_count = 0
    rom_files = []
    if db_ready:
        try:
            from Levenshtein import distance  # noqa: F401
            for fn in os.listdir(os.path.dirname(os.path.realpath(__file__))):
                fp = os.path.join(os.path.dirname(os.path.realpath(__file__)), fn)
                if os.path.isfile(fp):
                    ext = fn.split(".")[-1].lower()
                    if ext in config and fn not in PROJECT_MARKDOWN_FILES:
                        rom_count += 1
                        if len(rom_files) < 10:
                            rom_files.append(fn)
        except Exception:
            pass

    status_color = "#22c55e" if (db_ready and deps_ready) else "#f59e0b"
    status_text = "Ready" if (db_ready and deps_ready) else "Setting up…"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RetroArch CleanNaming — CLI Status</title>
<style>
  :root {{ --bg: #0f172a; --card: #1e293b; --accent: #6366f1; --text: #e2e8f0; --muted: #94a3b8; }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg); color: var(--text); min-height: 100vh;
    display: flex; align-items: center; justify-content: center; padding: 2rem;
  }}
  .card {{
    background: var(--card); border-radius: 1rem; padding: 2.5rem; max-width: 640px;
    width: 100%; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5);
  }}
  h1 {{ font-size: 1.75rem; margin-bottom: .5rem; }}
  .subtitle {{ color: var(--muted); margin-bottom: 2rem; }}
  .status {{ display: flex; align-items: center; gap: .6rem; margin-bottom: 1.5rem; }}
  .dot {{ width: 12px; height: 12px; border-radius: 50%; background: {status_color}; box-shadow: 0 0 12px {status_color}; }}
  .badge {{ background: var(--accent); color: #fff; padding: .25rem .75rem; border-radius: 999px; font-size: .8rem; font-weight: 600; }}
  .info-grid {{ display: grid; gap: 1rem; margin-bottom: 2rem; }}
  .info-row {{ display: flex; justify-content: space-between; padding: .75rem 1rem; background: rgba(255,255,255,0.03); border-radius: .5rem; }}
  .info-row .label {{ color: var(--muted); }}
  .info-row .value {{ font-weight: 600; text-align: right; }}
  .roms {{ margin-bottom: 1.5rem; }}
  .roms ul {{ list-style: none; margin-top: .5rem; }}
  .roms li {{ padding: .35rem 0; color: var(--muted); border-bottom: 1px solid rgba(255,255,255,0.05); }}
  code {{ background: rgba(99,102,241,0.15); color: #a5b4fc; padding: .15rem .4rem; border-radius: .25rem; font-size: .85rem; }}
  .cmd {{ background: #0f172a; border: 1px solid rgba(255,255,255,0.1); border-radius: .5rem; padding: 1rem; font-family: "SF Mono", Monaco, monospace; font-size: .85rem; margin-bottom: 1rem; overflow-x: auto; }}
  .cmd .prompt {{ color: var(--accent); }}
  .note {{ color: var(--muted); font-size: .85rem; margin-top: 1.5rem; line-height: 1.5; }}
</style>
</head>
<body>
  <div class="card">
    <h1>🎮 RetroArch CleanNaming</h1>
    <p class="subtitle">CLI tool that renames ROM files to match libretro database naming conventions.</p>

    <div class="status">
      <span class="dot"></span>
      <span>{status_text}</span>
      <span class="badge">CLI Tool</span>
    </div>

    <div class="info-grid">
      <div class="info-row"><span class="label">libretro-database</span><span class="value">{"✅ Cloned" if db_ready else "⏳ Cloning…"}</span></div>
      <div class="info-row"><span class="label">Python dependencies</span><span class="value">{"✅ Installed" if deps_ready else "⏳ Installing…"}</span></div>
      <div class="info-row"><span class="label">Supported extensions</span><span class="value">{escape(extensions)}</span></div>
      <div class="info-row"><span class="label">ROM files found</span><span class="value">{rom_count}</span></div>
    </div>
"""
    if rom_files:
        html += """    <div class="roms">
      <p class="label">Detected ROM files:</p>
      <ul>
"""
        for fn in rom_files:
            html += f"        <li>{escape(fn)}</li>\n"
        if rom_count > len(rom_files):
            html += f"        <li>… and {rom_count - len(rom_files)} more</li>\n"
        html += "      </ul>\n    </div>\n"

    html += """    <p class="label">Run the tool inside the container:</p>
    <div class="cmd"><span class="prompt">$</span> docker compose -f docker-compose.base44.yml exec app python3 rename_roms.py</div>
    <div class="cmd"><span class="prompt">$</span> docker compose -f docker-compose.base44.yml exec app python3 rename_roms.py -distance=15</div>

    <p class="note">
      Place ROM files in the <code>/app</code> directory (the repo root), then run the command above.
      The script matches by CRC32 first, then falls back to Levenshtein fuzzy matching.
      This page is a status dashboard — the actual tool runs in the terminal.
    </p>
  </div>
</body>
</html>"""
    return html


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = build_status_page().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # silence request logs


if __name__ == "__main__":
    with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print(f"Preview server running on port {PORT}")
        httpd.serve_forever()
