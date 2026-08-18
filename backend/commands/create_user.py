import argparse
import asyncio
import getpass

from sqlalchemy.exc import IntegrityError

from backend.modules.platform.application import PasswordService
from backend.modules.platform.infrastructure.postgres import UserRepository
from backend.shared.settings import load_settings
from backend.storage.pg import Database


async def create_user(username: str, password: str) -> None:
    settings = load_settings()
    database = Database()
    await database.connect(settings.database.url)
    try:
        async with database.session() as session:
            user = await UserRepository(session).create(username.strip(), PasswordService().hash(password))
        print(f"Created user {user.username} ({user.id})")
    finally:
        await database.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Karbi employee account")
    parser.add_argument("--username", required=True)
    args = parser.parse_args()
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Repeat password: ")
    if len(password) < 8:
        parser.error("password must contain at least 8 characters")
    if password != confirmation:
        parser.error("passwords do not match")
    try:
        asyncio.run(create_user(args.username, password))
    except IntegrityError:
        parser.error("username already exists")


if __name__ == "__main__":
    main()
