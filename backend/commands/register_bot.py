import argparse
import asyncio
import getpass

from backend.modules.notifications.application import BotRegistry
from backend.modules.notifications.domain import MessengerPermanentError, MessengerTemporaryError
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.notifications.infrastructure.relay import RelayClient
from backend.shared.settings import load_settings
from backend.storage.pg import Database


async def register_bot(code: str, title: str, token: str) -> None:
    settings = load_settings()
    relay = RelayClient(settings.relay)

    # The token goes straight through to the relay and is never written down on
    # this side: the relay stores it encrypted and answers with the bot's name
    # and the template invitation links are built from.
    identity = await relay.register_bot(bot_code=code, token=token)
    if not identity.invite_link_template:
        raise SystemExit("The relay returned no invite link template for this bot")

    database = Database()
    await database.connect(settings.database.url, pool_size=1, max_overflow=0)
    try:
        async with database.session() as session:
            registry = BotRegistry(session, NotificationRepository(session))
            bot = await registry.register(
                code=code,
                title=title or identity.title,
                invite_link_template=identity.invite_link_template,
            )
        print(f"Registered bot {bot.code} ({bot.id}) through the relay")
    finally:
        await database.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Register a notification bot on the relay")
    parser.add_argument("--code", required=True, help="stable id producers address, e.g. turnover-alerts")
    parser.add_argument("--title", default="", help="what subscribers see in the greeting")
    args = parser.parse_args()
    # getpass keeps the token out of the shell history and off the process list.
    token = getpass.getpass("Bot token: ").strip()
    if not token:
        parser.error("bot token is required")
    try:
        asyncio.run(register_bot(args.code.strip(), args.title.strip(), token))
    except MessengerPermanentError as error:
        parser.error(f"the relay rejected the token: {error}")
    except MessengerTemporaryError as error:
        parser.error(f"the relay is unreachable or unhappy: {error}")


if __name__ == "__main__":
    main()
