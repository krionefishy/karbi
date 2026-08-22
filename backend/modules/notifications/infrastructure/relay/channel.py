"""Verifying tokens the relay presents when it calls back.

The same shared secret signs both directions, so the audience is what keeps a
token minted for a call *to* the relay from being replayed *at* us.
"""

import jwt

from backend.shared.settings import RelayConfig


class ChannelAuthError(Exception):
    """The caller did not present a usable token."""


def verify_channel_token(header: str | None, config: RelayConfig) -> str:
    """Check an inbound Authorization header, returning the caller's subject."""
    if not header or not header.lower().startswith("bearer "):
        raise ChannelAuthError("missing bearer token")
    token = header.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(
            token,
            config.jwt_secret,
            algorithms=["HS256"],
            issuer=config.issuer,
            audience=config.inbound_audience,
            leeway=config.jwt_leeway_seconds,
            options={"require": ["sub", "jti", "iat", "exp", "iss", "aud"]},
        )
    except jwt.InvalidTokenError as error:
        raise ChannelAuthError(str(error)) from error
    return str(payload["sub"])
