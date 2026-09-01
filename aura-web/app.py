#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health"):
            body=json.dumps({"app":"Aura Voucher WiFi","status":"ok","note":"Starter web service. Omada 5.15 API integration comes next."},indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Content-Length",str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        self.send_error(404)

if __name__=="__main__":
    ThreadingHTTPServer(("127.0.0.1",8790),Handler).serve_forever()
