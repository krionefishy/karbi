"""Re-encrypt every stored secret with the current primary Fernet key.

Part of the key-rotation procedure described in docs/KEY_ROTATION.md: after the
new key is prepended to CREDENTIALS_ENCRYPTION_KEYS, this command rewrites the
ciphertexts in wb_core.credentials and notifications.bots so the old key can be
retired. MultiFernet.rotate decrypts with any listed key and encrypts with the
first one; plaintext values never leave the process.
"""

import argparse
import asyncio

from sqlalchemy import select

from backend.modules.wb_core.infrastructure.postgres.models import CredentialModel
from backend.shared.security import CredentialCipher
from backend.shared.settings import load_settings
from backend.storage.pg import Database


async def rotate_credentials(dry_run: bool) -> None:
    settings = load_settings()
    cipher = CredentialCipher(
        settings.security.credential_encryption_keys,
        settings.security.credential_fingerprint_key,
    )
    database = Database()
    await database.connect(settings.database.url, pool_size=1, max_overflow=0)
    try:
        async with database.session() as session:
            credentials = list(await session.scalars(select(CredentialModel)))
            for credential in credentials:
                credential.encrypted_api_key = cipher.rotate(credential.encrypted_api_key)
            # Bot tokens are not here any more: they live on the relay and are
            # rotated by its own key, which this server does not know.
            if dry_run:
                await session.rollback()
            else:
                await session.commit()
        action = "Would rotate" if dry_run else "Rotated"
        print(f"{action} {len(credentials)} wb_core.credentials rows")
    finally:
        await database.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-encrypt stored credentials with the primary Fernet key")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="decrypt and re-encrypt everything but roll the transaction back",
    )
    args = parser.parse_args()
    asyncio.run(rotate_credentials(args.dry_run))


if __name__ == "__main__":
    main()
