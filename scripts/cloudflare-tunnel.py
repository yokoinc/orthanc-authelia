#!/usr/bin/env python3
"""Create the Cloudflare tunnel that publishes this PACS, and its DNS record.

Called by bootstrap.sh when an API token is given, and usable on its own:

    CLOUDFLARE_API_TOKEN=... python3 scripts/cloudflare-tunnel.py --domain pacs.example.org

It prints the tunnel token on standard output -- that is what the cloudflared
container needs -- and what it is doing on standard error. Everything it does
is idempotent: a tunnel of the same name is reused, the DNS record is updated
rather than duplicated. Run it again after changing the address and the
routing follows.

The API token needs two permissions, and nothing else:
    Account -> Cloudflare Tunnel -> Edit
    Zone    -> DNS               -> Edit
It is never written anywhere: only the tunnel token it returns is.

Standard library only: it runs in a bare python container.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("CLOUDFLARE_API_BASE", "https://api.cloudflare.com/client/v4")


def log(message):
    print(message, file=sys.stderr, flush=True)


class CloudflareError(RuntimeError):
    pass


def call(token, method, path, payload=None):
    """One API call. Cloudflare answers 200 with success:false, so the body decides."""
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
        except Exception:  # noqa: BLE001 -- the status line is all we have
            raise CloudflareError(f"{method} {path}: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise CloudflareError(f"{method} {path}: {e.reason}") from e
    if not body.get("success", False):
        errors = "; ".join(
            f"{err.get('code', '?')} {err.get('message', '')}".strip()
            for err in body.get("errors") or []
        ) or "unknown error"
        raise CloudflareError(f"{method} {path}: {errors}")
    return body.get("result")


def find_zone(token, hostname):
    """The zone hosting this name: the longest zone name it ends with.

    pacs.example.org belongs to example.org; a sub-zone (pacs.example.org as a
    zone of its own) wins over its parent, hence the longest match.
    """
    zones = call(token, "GET", "/zones?per_page=50") or []
    matches = [
        z for z in zones
        if hostname == z["name"] or hostname.endswith("." + z["name"])
    ]
    if not matches:
        known = ", ".join(z["name"] for z in zones) or "none"
        raise CloudflareError(
            f"no zone of this account covers {hostname} (zones seen: {known}). "
            "The domain must be managed by Cloudflare, and the token must give "
            "Zone -> DNS -> Edit on it."
        )
    zone = max(matches, key=lambda z: len(z["name"]))
    return zone["id"], zone["name"], zone["account"]["id"]


def tunnel_named(token, account, name):
    tunnels = call(token, "GET", f"/accounts/{account}/cfd_tunnel?name={name}&is_deleted=false") or []
    return tunnels[0] if tunnels else None


def ensure_tunnel(token, account, name):
    """The tunnel, created if needed. Returns (id, tunnel token)."""
    existing = tunnel_named(token, account, name)
    if existing:
        log(f"  tunnel {name}: already exists, reused")
        tunnel_id = existing["id"]
    else:
        created = call(token, "POST", f"/accounts/{account}/cfd_tunnel",
                       {"name": name, "config_src": "cloudflare"})
        tunnel_id = created["id"]
        log(f"  tunnel {name}: created")
    return tunnel_id, call(token, "GET", f"/accounts/{account}/cfd_tunnel/{tunnel_id}/token")


def configure_tunnel(token, account, tunnel_id, hostname, service):
    """Route the hostname to nginx, with the three settings this stack needs.

    noTLSVerify: nginx serves a self-signed certificate on a hop that never
    leaves the Docker network. httpHostHeader: Authelia and the panel decide on
    the Host they receive. http2Origin: keeps the viewer's parallel requests on
    one connection.
    """
    call(token, "PUT", f"/accounts/{account}/cfd_tunnel/{tunnel_id}/configurations", {
        "config": {
            "ingress": [
                {
                    "hostname": hostname,
                    "service": service,
                    "originRequest": {
                        "noTLSVerify": True,
                        "httpHostHeader": hostname,
                        "http2Origin": True,
                    },
                },
                {"service": "http_status:404"},
            ],
        },
    })
    log(f"  route: {hostname} -> {service} (no TLS verify, Host header, HTTP/2)")


def ensure_dns(token, zone_id, hostname, tunnel_id):
    """CNAME hostname -> <tunnel>.cfargotunnel.com, proxied. Updated if present."""
    target = f"{tunnel_id}.cfargotunnel.com"
    record = {"type": "CNAME", "name": hostname, "content": target, "proxied": True,
              "comment": "orthanc-authelia: tunnel to the PACS"}
    existing = call(token, "GET", f"/zones/{zone_id}/dns_records?name={hostname}") or []
    if existing:
        call(token, "PUT", f"/zones/{zone_id}/dns_records/{existing[0]['id']}", record)
        log(f"  DNS: {hostname} updated -> {target}")
    else:
        call(token, "POST", f"/zones/{zone_id}/dns_records", record)
        log(f"  DNS: {hostname} created -> {target}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--domain", required=True, help="public name of the PACS")
    parser.add_argument("--service", default="https://nginx:443",
                        help="what the tunnel talks to (default: the nginx container)")
    parser.add_argument("--name", default=None, help="tunnel name (default: pacs-<domain>)")
    args = parser.parse_args(argv)

    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not token:
        log("CLOUDFLARE_API_TOKEN is empty.")
        return 2
    name = args.name or "pacs-" + args.domain.replace(".", "-")

    try:
        call(token, "GET", "/user/tokens/verify")
        zone_id, zone_name, account = find_zone(token, args.domain)
        log(f"  zone {zone_name}: found")
        tunnel_id, tunnel_token = ensure_tunnel(token, account, name)
        configure_tunnel(token, account, tunnel_id, args.domain, args.service)
        ensure_dns(token, zone_id, args.domain, tunnel_id)
    except CloudflareError as e:
        log(f"Cloudflare refused: {e}")
        return 1

    print(tunnel_token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
