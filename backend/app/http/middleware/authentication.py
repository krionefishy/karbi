from typing import Protocol

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from backend.app.http.authentication import AuthenticatedPrincipal
from backend.modules.platform.application import AccessClaims, AuthenticationError


class AccessTokenDecoder(Protocol):
    def decode_access_claims(self, token: str) -> AccessClaims: ...

    async def is_access_revoked(self, jti: str) -> bool: ...


class AccessTokenMiddleware:
    """Passively attach a verified JWT principal without requiring authentication."""

    def __init__(self, app: ASGIApp, *, decoder: AccessTokenDecoder) -> None:
        self.app = app
        self._decoder = decoder

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        authorization = Headers(scope=scope).get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            try:
                claims = self._decoder.decode_access_claims(token.strip())
            except AuthenticationError:
                pass
            else:
                if not await self._decoder.is_access_revoked(claims.jti):
                    scope.setdefault("state", {})["principal"] = AuthenticatedPrincipal(user_id=claims.user_id)
        await self.app(scope, receive, send)
