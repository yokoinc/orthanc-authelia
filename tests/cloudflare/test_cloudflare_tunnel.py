"""The Cloudflare helper, against a stub of the API.

No network: a small HTTP server answers like Cloudflare does, including its
habit of returning HTTP 200 with success:false. What is checked is what the
installation depends on -- the right zone, one tunnel reused rather than
duplicated, the three origin settings, the CNAME to the tunnel, and a clear
message when the token cannot do the job.
"""
import json
import os
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "cloudflare-tunnel.py"

state = {"tunnels": {}, "dns": {}, "calls": [], "zones": [
    {"id": "zone-org", "name": "example.org", "account": {"id": "acct-1"}},
    {"id": "zone-sub", "name": "pacs.example.org", "account": {"id": "acct-1"}},
    {"id": "zone-other", "name": "autre.fr", "account": {"id": "acct-1"}},
]}


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, result, success=True, errors=None):
        body = json.dumps({"success": success, "result": result, "errors": errors or []})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body.encode())

    def _payload(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length)) if length else None

    def do_GET(self):  # noqa: N802 -- http.server API
        state["calls"].append(("GET", self.path))
        if self.headers.get("Authorization") != "Bearer good-token":
            return self._send(None, False, [{"code": 1000, "message": "Invalid API Token"}])
        if self.path.startswith("/user/tokens/verify"):
            return self._send({"status": "active"})
        if self.path.startswith("/zones"):
            return self._send(state["zones"])
        if "/cfd_tunnel?" in self.path:
            name = self.path.split("name=")[1].split("&")[0]
            found = [t for t in state["tunnels"].values() if t["name"] == name]
            return self._send(found)
        if self.path.endswith("/token"):
            return self._send("tunnel-token-eyJ")
        if "/dns_records?" in self.path:
            name = self.path.split("name=")[1].split("&")[0]
            return self._send([r for r in state["dns"].values() if r["name"] == name])
        return self._send(None, False, [{"code": 404, "message": "not stubbed: " + self.path}])

    def do_POST(self):  # noqa: N802
        payload = self._payload()
        state["calls"].append(("POST", self.path, payload))
        if "/cfd_tunnel" in self.path:
            tunnel = {"id": "tunnel-1", "name": payload["name"]}
            state["tunnels"][tunnel["id"]] = tunnel
            return self._send(tunnel)
        if "/dns_records" in self.path:
            record = dict(payload, id="dns-1")
            state["dns"][record["id"]] = record
            return self._send(record)
        return self._send(None, False, [{"code": 404, "message": "not stubbed"}])

    def do_PUT(self):  # noqa: N802
        payload = self._payload()
        state["calls"].append(("PUT", self.path, payload))
        if "/configurations" in self.path:
            return self._send({"config": payload["config"]})
        if "/dns_records/" in self.path:
            record_id = self.path.rsplit("/", 1)[1]
            state["dns"][record_id] = dict(payload, id=record_id)
            return self._send(state["dns"][record_id])
        return self._send(None, False, [{"code": 404, "message": "not stubbed"}])


class CloudflareHelperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        state["tunnels"].clear()
        state["dns"].clear()
        state["calls"].clear()

    def run_helper(self, domain="pacs.example.org", token="good-token", extra=()):
        env = dict(os.environ, CLOUDFLARE_API_BASE=self.base, CLOUDFLARE_API_TOKEN=token)
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--domain", domain, *extra],
            capture_output=True, text=True, env=env, timeout=60,
        )

    def test_creates_tunnel_route_and_dns(self):
        r = self.run_helper()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "tunnel-token-eyJ", "the tunnel token is what bootstrap reads")

        config = next(c[2]["config"] for c in state["calls"] if c[0] == "PUT" and "/configurations" in c[1])
        rule = config["ingress"][0]
        self.assertEqual(rule["hostname"], "pacs.example.org")
        self.assertEqual(rule["service"], "https://nginx:443")
        self.assertTrue(rule["originRequest"]["noTLSVerify"], "nginx serves a self-signed certificate")
        self.assertEqual(rule["originRequest"]["httpHostHeader"], "pacs.example.org")
        self.assertTrue(rule["originRequest"]["http2Origin"])
        self.assertEqual(config["ingress"][-1]["service"], "http_status:404", "a catch-all rule is required")

        record = list(state["dns"].values())[0]
        self.assertEqual((record["type"], record["name"]), ("CNAME", "pacs.example.org"))
        self.assertEqual(record["content"], "tunnel-1.cfargotunnel.com")
        self.assertTrue(record["proxied"])

    def test_the_most_specific_zone_wins(self):
        self.run_helper(domain="pacs.example.org")
        self.assertTrue(any("/zones/zone-sub/dns_records" in c[1] for c in state["calls"]),
                        "pacs.example.org is a zone of its own here")

    def test_run_twice_reuses_the_tunnel_and_updates_the_record(self):
        self.run_helper()
        self.run_helper()
        created = [c for c in state["calls"] if c[0] == "POST" and "/cfd_tunnel" in c[1]]
        self.assertEqual(len(created), 1, "a second run must not create a second tunnel")
        self.assertEqual(len(state["dns"]), 1, "nor a second DNS record")

    def test_a_domain_outside_the_account_is_explained(self):
        r = self.run_helper(domain="pacs.ailleurs.com")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no zone of this account covers", r.stderr)
        self.assertIn("autre.fr", r.stderr, "the zones actually seen help to spot a typo")

    def test_a_bad_token_is_explained(self):
        r = self.run_helper(token="wrong")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Invalid API Token", r.stderr)
        self.assertEqual(r.stdout.strip(), "", "nothing must reach .env")


if __name__ == "__main__":
    unittest.main()
