"""Начальные данные стенда: администратор и два менеджера. Идемпотентно.

Логины и пароли берутся из /stand/.credentials.txt (его пишет deploy/stand/stand.sh на хосте, строки
`роль логин пароль`): нет пользователя — создаётся, есть — пароль и флаг админа приводятся к файлу.
Пароли в вывод не попадают.

Доступ к сервисам выдаётся каждому сотруднику (platform.user_service_access): менеджерам стенда seed
выдаёт «Контроль скриншотов ДБС» от имени администратора стенда, как это сделал бы админ в диалоге
доступов. Администратор видит все сервисы без выдачи.

    python /stand/seed.py [/stand/.credentials.txt]
"""

import asyncio
import sys
from pathlib import Path

from backend.modules.platform.application import PasswordService
from backend.modules.platform.infrastructure.postgres import UserRepository
from backend.shared.settings import load_settings
from backend.storage.pg import Database

SERVICE_ID = "wb-screenshot-control"


def read_credentials(path: Path) -> list[tuple[str, str, str]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 3 and not line.startswith("#"):
            rows.append((parts[0], parts[1], parts[2]))
    if not rows:
        raise SystemExit(f"В {path} нет строк «роль логин пароль»")
    return rows


async def seed(path: Path) -> None:
    rows = read_credentials(path)
    settings = load_settings()
    database = Database()
    await database.connect(settings.database.url, pool_size=1, max_overflow=0)
    passwords = PasswordService()
    try:
        async with database.session() as session:
            users = UserRepository(session)
            for role, username, password in rows:
                is_admin = role == "admin"
                user = await users.get_by_username(username)
                if user is None:
                    user = await users.create(username, passwords.hash(password), is_admin=is_admin)
                    action = "создан"
                else:
                    await users.set_password(user.id, passwords.hash(password))
                    await users.set_admin(user.id, is_admin)
                    await users.set_active(user.id, True)
                    action = "обновлён"
                print(f"{role:8} {username:20} {action}")
            await session.commit()
            admins = [username for role, username, _ in rows if role == "admin"]
            granter = await users.get_by_username(admins[0]) if admins else None
            if granter is None:
                raise SystemExit("Нужен администратор в файле логинов: от его имени выдаётся доступ к сервису")
            for role, username, _ in rows:
                user = await users.get_by_username(username)
                if role != "admin" and user is not None:
                    await users.replace_services(user.id, await users.service_ids(user.id) | {SERVICE_ID}, granter.id)
                    print(f"{role:8} {username:20} доступ к {SERVICE_ID}")
    finally:
        await database.disconnect()


if __name__ == "__main__":
    asyncio.run(seed(Path(sys.argv[1] if len(sys.argv) > 1 else "/stand/.credentials.txt")))
