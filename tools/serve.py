"""Serve _site the way GitHub Pages does: under /<repo>/, nothing at the root."""
import functools, http.server, os, sys
PREFIX = "/Pilot_Demo"
SITE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_site")
class Handler(http.server.SimpleHTTPRequestHandler):
  def translate_path(self, path):
    if not path.startswith(PREFIX + "/"):
      return os.path.join(SITE, "__nope__")
    return super().translate_path(path[len(PREFIX):])
  def end_headers(self):
    self.send_header("Cache-Control", "no-cache")
    super().end_headers()
  def log_message(self, fmt, *args):
    if " 404 " in (fmt % args) or "404" in str(args[1:2]):
      sys.stderr.write("404 " + (fmt % args) + "\n")
port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(Handler, directory=SITE)).serve_forever()
