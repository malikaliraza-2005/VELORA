"""A stand-in for the founder's alert webhook, for the stack rehearsal (stack-drill.sh).

An HTTPS server (the monitor refuses plain http outside development) that prints each JSON
alert it receives as one line: `ALERT {...}`.
"""

import http.server
import json
import ssl
import sys


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - the http.server API
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        print("ALERT " + json.dumps(body, sort_keys=True), flush=True)
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


def main() -> None:
    cert, key = sys.argv[1], sys.argv[2]
    server = http.server.ThreadingHTTPServer(("0.0.0.0", 8443), Handler)  # noqa: S104
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    print("alert sink ready", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
