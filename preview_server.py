#!/usr/bin/env python3
"""Minimal status page so the Base44 preview has something to serve on port 3000.

This is a CLI tool (rename_roms.py), not a web app.  The preview still needs an
HTTP endpoint, so we serve a simple status page that reports whether the
environment is ready and how to run the script from inside the container.
"""
import html
import http.server
import os
import socketserver


def _env_ready() -> bool:
    try:
        import Levenshtein  # noqa: F401
    except Exception:
        return False
    return os.path.isdir("/app/libretro-database/.git")


def _page() -> str:
    ready = _env_ready()
    status = "Ready" if ready else "Setting up\u2026"
    color = "#16a34a" if ready else "#d97706"
    body = (
        "<h1>RetroArch CleanNaming</h1>"
        f"<p class='status' style='color:{color}'>Status: {html.escape(status)}</p>"
        "<p>This is a CLI tool that renames ROM files to match the "
        "<a href='https://github.com/libretro/libretro-database'>libretro database</a> "
        "naming conventions.</p>"
        "<h2>Usage</h2>"
        "<pre>python3 rename_roms.py            # default distance threshold\n"
        "python3 rename_roms.py -distance=15  # custom fuzzy match</pre>"
        "<p>Place your ROM files in the working directory "
        "(<code>/app</code> inside the container), then run the script.</p>"
        "<p>See <code>config.json</code> for the extension-to-database mapping.</p>"
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>RetroArch CleanNaming</title>"
        "<style>"
        "body{font-family:system-ui,sans-serif;max-width:680px;margin:40px auto;"
        "padding:0 20px;color:#1f2937}"
        "h1{margin-bottom:8px}"
        ".status{font-weight:600;font-size:1.1rem}"
        "pre{background:#f3f4f6;padding:14px;border-radius:8px;overflow-x:auto}"
        "code{background:#f3f4f6;padding:2px 5px;border-radius:4px}"
        "a{color:#2563eb}"
        "</style></head><body>" + body + "</body></html>"
    )


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/":
            self.send_response(404)
            self.end_headers()
            return
        body = _page().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence default logging
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "3000"))
    with socketserver.TCPServer(("0.0.0.0", port), Handler) as httpd:
        httpd.serve_forever()
