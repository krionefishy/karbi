import argparse
import asyncio
import getpass

from backend.modules.notifications.application import BotRegistry, DuplicateBotError
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.notifications.infrastructure.telegram import TelegramClient, TelegramPermanentError
from backend.shared.security import CredentialCipher
from backend.shared.settings import load_settings
from backend.storage.pg import Database


async def register_bot(code: str, title: str, token: str) -> None:
    settings = load_settings()
    client = TelegramClient(
        base_url=settings.telegram.api_base_url,
        request_timeout_seconds=settings.telegram.request_timeout_seconds,
        poll_timeout_seconds=settings.telegram.poll_timeout_seconds,
    )
    # Ask Telegram who this token belongs to: it validates the token and gives
    # the username the invitation links are built from.
    identity = await client.me(token)
    username = str(identity.get("username") or "")
    if not username:
        raise SystemExit("Telegram did not return a username for this token")

    database = Database()
    await database.connect(settings.database.url, pool_size=1, max_overflow=0)
    try:
        async with database.session() as session:
            repository = NotificationRepository(session)
            registry = BotRegistry(
                session,
                repository,
                CredentialCipher(
                    settings.security.credential_encryption_keys,
                    settings.security.credential_fingerprint_key,
                ),
            )
            bot = await registry.register(code=code, username=username, title=title, token=token)
        print(f"Registered bot {bot.code} as @{bot.username} ({bot.id})")
    finally:
        await database.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Register a Telegram bot for notifications")
    parser.add_argument("--code", required=True, help="stable id producers address, e.g. turnover-alerts")
    parser.add_argument("--title", default="", help="what subscribers see in the greeting")
    args = parser.parse_args()
    token = getpass.getpass("Bot token: ").strip()
    if not token:
        parser.error("bot token is required")
    try:
        asyncio.run(register_bot(args.code.strip(), args.title.strip(), token))
    except DuplicateBotError as error:
        parser.error(str(error))
    except TelegramPermanentError as error:
        parser.error(f"Telegram rejected the token: {error}")


if __name__ == "__main__":
    main()
