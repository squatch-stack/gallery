#!/usr/bin/env python3
"""Serve the gallery plus a wrapper page that holds `load` open until a scene has drawn.

Headless Chrome fires `--screenshot` on the load event, which for this viewer
arrives long before a SOG has been fetched, decoded and rendered — every frame
came out as the loading spinner at 0%. `--virtual-time-budget` does not help:
it fast-forwards timers past the decode rather than waiting for it.

So the wrapper at /shot.html embeds the viewer full-window and also requests
/slow, an endpoint that simply does not answer for `ms` milliseconds. The load
event cannot fire until it does, which gives the splat time to appear, and
Chrome then captures a drawn frame.

    python3 tools/shot_server.py --root . --port 8875
    chrome --headless=new --window-size=900,900 --screenshot=f.png \
        'http://localhost:8875/shot.html?scene=cannon&az=30&el=10&d=2.2&ms=14000'
"""
from __future__ import annotations

import argparse
import functools
import http.server
import socketserver
import threading
import time
import urllib.parse

WRAPPER = """<!doctype html><html><head><meta charset="utf-8"><title>shot</title>
<style>html,body{{margin:0;height:100%;background:#000;overflow:hidden}}
iframe{{border:0;width:100vw;height:100vh;display:block}}
img{{position:absolute;width:1px;height:1px;opacity:0;pointer-events:none}}</style>
</head><body><iframe id="v" src="{src}"></iframe><img src="/slow?ms={ms}" alt="">
<script>
// Same origin, so the back link and caption can be hidden inside the frame.
// A turntable frame wants the subject and nothing else.
var bare = {bare};
if (bare) {{
  var f = document.getElementById('v');
  var hide = function () {{
    try {{
      var d = f.contentDocument;
      if (!d) return;
      var st = d.getElementById('shot-bare') || d.createElement('style');
      st.id = 'shot-bare';
      st.textContent = '#back,#hud,#load{{display:none!important}}';
      d.head.appendChild(st);
    }} catch (e) {{}}
  }};
  f.addEventListener('load', hide);
  setInterval(hide, 250);
}}
</script></body></html>"""

MAX_WAIT_MS = 120_000


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/slow':
            query = urllib.parse.parse_qs(parsed.query)
            try:
                ms = int(query.get('ms', ['8000'])[0])
            except ValueError:
                ms = 8000
            time.sleep(max(0, min(ms, MAX_WAIT_MS)) / 1000)
            body = bytes.fromhex('47494638396101000100800000000000ffffff21f9040100000'
                                 '02c00000000010001000002024401003b')
            self.send_response(200)
            self.send_header('Content-Type', 'image/gif')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == '/shot.html':
            query = urllib.parse.parse_qs(parsed.query)
            ms = query.pop('ms', ['8000'])[0]
            try:
                ms = str(max(0, min(int(ms), MAX_WAIT_MS)))
            except ValueError:
                ms = '8000'
            bare = '1' if query.pop('bare', ['0'])[0] not in ('0', '', 'false') else '0'
            inner = urllib.parse.urlencode({k: v[0] for k, v in query.items()})
            page = WRAPPER.format(src='viewer.html?' + inner, ms=ms, bare=bare).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(page)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(page)
            return
        super().do_GET()

    def log_message(self, *args):
        pass


class Server(socketserver.ThreadingTCPServer):
    # Threaded: /slow blocks its own handler for seconds and must not stall the
    # viewer's own requests for the SOG.
    daemon_threads = True
    allow_reuse_address = True


def serve(root, port):
    handler = functools.partial(Handler, directory=str(root))
    httpd = Server(('127.0.0.1', port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--root', default='.')
    p.add_argument('--port', type=int, default=8875)
    args = p.parse_args(argv)
    httpd = serve(args.root, args.port)
    print(f'serving {args.root} on http://127.0.0.1:{args.port} (/shot.html, /slow)')
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == '__main__':
    main()
