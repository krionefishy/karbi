"""One-off migration: deliver every active seller's key to the wb-egress gateway.

The gateway (docs/architecture/WB_EGRESS.md) becomes the only holder of seller
keys. This command walks wb_core.sellers, decrypts each key with the same
cipher the application uses and PUTs it to the gateway over the pinned TLS
channel. The seller_id on the gateway is the seller's existing UUID from this
database — nothing is deleted or re-created here, and every linked automation
keeps its data.

Plaintext keys exist only in this process's memory and inside the TLS channel:
they are never printed, logged or written anywhere.

Run inside the api container on prod:

    docker compose exec \
        -e EGRESS_JWT_SECRET=... api \
        python -m backend.commands.export_sellers_to_egress \
        --egress-url https://157.22.230.67:8443 --verify /tmp/egress.crt
"""

import argparse
import asyncio
import os
import ssl
import sys
import time

import httpx
import jwt
from sqlalchemy import select

from backend.modules.wb_core.infrastructure.postgres.models import CredentialModel, SellerModel
from backend.shared.security import CredentialCipher
from backend.shared.settings import load_settings
from backend.storage.pg import Database

_AUDIENCE = "wb-egress:karbi"


def _bearer(secret: str) -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode({"aud": _AUDIENCE, "iat": now, "exp": now + 300}, secret, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def _tls_context(verify_path: str) -> ssl.SSLContext | bool:
    """Pin the gateway's self-signed certificate.

    The connection goes to the VPS by IP while the certificate names
    `egress.internal`, so hostname checking is off — trust comes from pinning
    this exact certificate, not from a name in a public PKI.
    """
    if not verify_path:
        return True
    context = ssl.create_default_context(cafile=verify_path)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    return context


async def export(egress_url: str, jwt_secret: str, verify_path: str, dry_run: bool) -> int:
    settings = load_settings()
    cipher = CredentialCipher(
        settings.security.credential_encryption_keys,
        settings.security.credential_fingerprint_key,
    )
    database = Database()
    await database.connect(settings.database.url, pool_size=1, max_overflow=0)
    failures = 0
    version = int(time.time())
    try:
        async with database.session() as session:
            rows = list(
                await session.execute(
                    select(SellerModel, CredentialModel)
                    .join(CredentialModel, CredentialModel.seller_id == SellerModel.id)
                    .where(SellerModel.archived_at.is_(None))
                    .order_by(SellerModel.name)
                )
            )
        if dry_run:
            for seller, _ in rows:
                print(f"would export: {seller.id}  {seller.name}")
            print(f"{len(rows)} sellers total, nothing sent")
            return 0
        async with httpx.AsyncClient(
            base_url=egress_url.rstrip("/"),
            verify=_tls_context(verify_path),
            timeout=httpx.Timeout(120.0, connect=10.0),
        ) as client:
            for seller, credential in rows:
                response = await client.put(
                    f"/api/v1/sellers/{seller.id}",
                    json={
                        "name": seller.name,
                        "api_key": cipher.decrypt(credential.encrypted_api_key),
                        "event_version": version,
                    },
                    headers=_bearer(jwt_secret),
                )
                if response.status_code != 200:
                    failures += 1
                    print(f"FAIL  {seller.id}  {seller.name}: HTTP {response.status_code} {response.text[:200]}")
                    continue
                payload = response.json()
                print(f"{payload['status']:<12} {payload['egress_ip'] or '-':<16} {seller.id}  {seller.name}")
                if payload["status"] not in {"verified", "delivered"}:
                    failures += 1
    finally:
        await database.disconnect()
    tail = f", {failures} need attention" if failures else ""
    print(f"exported {len(rows) - failures} of {len(rows)} sellers{tail}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Deliver seller keys to the wb-egress gateway")
    parser.add_argument("--egress-url", default=os.environ.get("EGRESS_URL", ""), help="e.g. https://<vps-ip>:8443")
    parser.add_argument("--verify", default=os.environ.get("EGRESS_VERIFY", ""), help="path to the pinned egress.crt")
    parser.add_argument("--dry-run", action="store_true", help="list the sellers that would be sent, send nothing")
    args = parser.parse_args()
    # The secret comes from the environment only: argv is visible in `ps`.
    jwt_secret = os.environ.get("EGRESS_JWT_SECRET", "")
    if not args.dry_run and not args.egress_url:
        parser.error("--egress-url (or EGRESS_URL) is required")
    if not args.dry_run and not jwt_secret:
        parser.error("EGRESS_JWT_SECRET must be set in the environment")
    return asyncio.run(export(args.egress_url, jwt_secret, args.verify, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
