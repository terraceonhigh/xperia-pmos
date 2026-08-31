#!/usr/bin/env python3
"""A minimal HTTP forward proxy, so the phone can reach apk repositories.

Under USB-ECM the phone can talk to this laptop and nothing else. The usual fix is
NAT on the host, but on macOS that means enabling IP forwarding and replacing the pf
ruleset -- both need root and both touch the user's network configuration.

A forward proxy avoids all of it: `apk` honours http_proxy, every repository in
/etc/apk/repositories is plain http (so no CONNECT tunnelling is needed), and the
Mac does the DNS and the fetching. Nothing on the host changes, and stopping the
process removes the phone's access.

Usage:
    ./apk-proxy.py [--host 172.16.42.2] [--port 8080]
then on the phone:
    export http_proxy=http://172.16.42.2:8080
"""

import argparse
import shutil
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CHUNK = 64 * 1024


class Proxy(BaseHTTPRequestHandler):
    # HTTP/1.0 on purpose. Upstream mirrors answer some requests with chunked
    # transfer-encoding; forwarding that as HTTP/1.1 without re-chunking leaves the
    # client unable to find the end of the body (apk hangs up, and this side sees a
    # broken pipe). With 1.0 the body is delimited by closing the connection, which
    # needs no re-framing at all.
    protocol_version = "HTTP/1.0"
    server_version = "apk-proxy"

    def log_message(self, fmt, *args):
        # One line per request, to stderr, so a long apk run stays readable.
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _forward(self, method):
        # In a proxy request the path is an absolute URI. Refuse anything else
        # rather than guessing a host.
        if not self.path.startswith("http://"):
            self.send_error(400, "proxy requests only (absolute http:// URI)")
            return
        req = urllib.request.Request(self.path, method=method)
        for h in ("range", "if-modified-since", "user-agent"):
            if h in self.headers:
                req.add_header(h, self.headers[h])
        try:
            with urllib.request.urlopen(req, timeout=60) as up:
                self.send_response(up.status)
                for k, v in up.headers.items():
                    # Hop-by-hop headers must not be forwarded; length/encoding are
                    # set from what we actually send.
                    if k.lower() in ("connection", "transfer-encoding",
                                     "keep-alive", "proxy-authenticate",
                                     "content-length"):
                        # content-length is dropped with the rest: the body is
                        # close-delimited, and a forwarded length that disagreed
                        # with what we actually write would be worse than none.
                        continue
                    self.send_header(k, v)
                self.send_header("Connection", "close")
                self.end_headers()
                if method == "GET":
                    shutil.copyfileobj(up, self.wfile, CHUNK)
        except urllib.error.HTTPError as e:
            # Pass the upstream status through: apk relies on 404s while probing.
            body = e.read()
            self.send_response(e.code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if method == "GET":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # The client gave up; nothing useful to send and nowhere to send it.
            pass
        except Exception as e:                                  # noqa: BLE001
            try:
                self.send_error(502, f"upstream failed: {e}")
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_GET(self):
        self._forward("GET")

    def do_HEAD(self):
        self._forward("HEAD")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="172.16.42.2",
                    help="address to bind (default: this Mac's USB-ECM address)")
    ap.add_argument("--port", type=int, default=8080)
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Proxy)
    srv.daemon_threads = True
    print(f"proxying for the phone on http://{a.host}:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
