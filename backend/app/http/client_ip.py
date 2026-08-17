from fastapi import Request


def resolve_client_ip(request: Request, *, behind_trusted_proxy: bool) -> str:
    """Return the socket peer, or the IP set by our trusted reverse proxy."""
    socket_peer = request.client.host if request.client else "unknown"
    if not behind_trusted_proxy:
        return socket_peer
    return request.headers.get("x-real-ip", socket_peer).strip()
