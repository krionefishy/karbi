import uuid

from backend.app.factory import Application
from backend.modules.platform.application import PasswordService
from backend.modules.platform.infrastructure.postgres import UserRepository

PREFIX = "fixture-operator-"


async def operator_token(application: Application) -> str:
    """Токен настоящего сотрудника: рабочие маршруты сверяют пользователя и доступ к сервису в базе.

    Сотрудник администратор, поэтому видит все сервисы, и новый сервис не требует правки каждого фикстура.
    Строка остаётся в тестовой базе: она одноразовая и снимается вместе с ней.
    """
    async with application.database.session() as session:
        user = await UserRepository(session).create(
            f"{PREFIX}{uuid.uuid4().hex}", PasswordService().hash(uuid.uuid4().hex), is_admin=True
        )
    return application.token_service.issue_access(user.id)
