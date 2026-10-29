"""Начальные данные стенда: администратор и два менеджера. Идемпотентно.

Логины и пароли берутся из /stand/.credentials.txt (его пишет deploy/stand/stand.sh на хосте, строки
`роль логин пароль`): нет пользователя — создаётся, есть — пароль и флаг админа приводятся к файлу.
Пароли в вывод не попадают.

В этой ветке нет доступа к сервисам по сотрудникам (platform.user_service_access появился в main позже):
«Контроль скриншотов ДБС» открыт любому вошедшему, админка — флагу is_admin. Выдавать доступ не нужно.

    python /stand/seed.py [/stand/.credentials.txt]
"""

import asyncio
import sys
from pathlib import Path

from backend.modules.platform.application import PasswordService
from backend.modules.platform.infrastructure.postgres import UserRepository
from backend.shared.settings import load_settings
from backend.storage.pg import Database


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
    finally:
        await database.disconnect()


if __name__ == "__main__":
    asyncio.run(seed(Path(sys.argv[1] if len(sys.argv) > 1 else "/stand/.credentials.txt")))
