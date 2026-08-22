"""Grant or revoke the admin flag for an account that already exists.

`create_user --admin` covers the very first administrator; this covers everyone
after, including the case where the only administrator left the company. It is
the escape hatch for the panel locking itself: the section that hands out the
flag is behind the flag.
"""

import argparse
import asyncio

from backend.modules.platform.infrastructure.postgres import UserRepository
from backend.shared.settings import load_settings
from backend.storage.pg import Database


async def grant_admin(username: str, is_admin: bool) -> None:
    settings = load_settings()
    database = Database()
    await database.connect(settings.database.url, pool_size=1, max_overflow=0)
    try:
        async with database.session() as session:
            users = UserRepository(session)
            user = await users.get_by_username(username.strip())
            if user is None:
                raise SystemExit(f"No such user: {username}")
            updated = await users.set_admin(user.id, is_admin)
        state = "is now an admin" if updated and updated.is_admin else "is no longer an admin"
        print(f"{username} {state}")
    finally:
        await database.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Grant or revoke access to the admin section")
    parser.add_argument("--username", required=True)
    parser.add_argument("--revoke", action="store_true", help="take the flag away instead of granting it")
    args = parser.parse_args()
    asyncio.run(grant_admin(args.username, not args.revoke))


if __name__ == "__main__":
    main()
