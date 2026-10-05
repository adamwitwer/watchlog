"""Keeps the Pi's two listeners off the internet, whatever the router does.

The webhook and the admin page bind 0.0.0.0 because the address they would
otherwise bind to is not stable: the Pi's LAN address is a DHCP lease, and a
service bound to last month's lease fails to start with nothing on the page to
say why. So the address is checked per request instead.

That catches the ways the Pi actually ends up reachable from outside. A port
forward or a UPnP mapping rewrites the destination, not the source, so the
request still arrives from a public address and is refused here. Nothing proxies
in front of these apps, so remote_addr is the real peer and is not
caller-supplied.

Refusals answer 404, like a wrong token: the page should not confirm to a
stranger that there is anything here at all.
"""
import ipaddress
import logging

import waitress
from flask import request

log = logging.getLogger("watchlog.lan")

# Tailscale hands out addresses from the CGNAT block, which Python does not
# count as private. Its IPv6 range is a ULA, which it does.
TAILSCALE = ipaddress.ip_network("100.64.0.0/10")


def is_local(addr):
    """True for loopback, the LAN, and the Tailnet. False for anything else,
    including an address that does not parse."""
    try:
        ip = ipaddress.ip_address(addr or "")
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_private or ip in TAILSCALE


def restrict(app):
    """Refuse every request to `app` that does not come from a local address."""
    @app.before_request
    def _local_only():
        if not is_local(request.remote_addr):
            log.warning("refused %s %s from %s", request.method, request.path,
                        request.remote_addr)
            return "Not found", 404
    return app


def serve(app, port):
    """Run `app` under waitress rather than Flask's development server.

    Still 0.0.0.0, for the DHCP reason above: restrict() is the gate, not the
    bind address. waitress is given no trusted_proxy, so it leaves
    X-Forwarded-For alone and remote_addr stays the real peer, which is the
    one thing restrict() depends on. The body cap is repeated here so an
    oversized request is refused while it is still arriving, not after Flask
    has buffered it, and ident=None drops the Server header.
    """
    waitress.serve(
        app,
        host="0.0.0.0",
        port=port,
        threads=4,
        max_request_body_size=app.config.get("MAX_CONTENT_LENGTH") or 1024 * 1024,
        ident=None,
    )
