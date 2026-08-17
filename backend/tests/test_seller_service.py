import uuid
from types import SimpleNamespace
from typing import cast

from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import SellerService
from backend.modules.wb_core.domain import Article, Seller
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import OutboxEventModel
from backend.shared.security import CredentialCipher


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1


class FakeSellerRepository:
    def __init__(self) -> None:
        self.seller_id = uuid.uuid4()
        self.model = SimpleNamespace(
            id=self.seller_id, name="Селлер", catalog_sync_status="success", catalog_sync_error=None
        )
        self.credential = SimpleNamespace(encrypted_api_key="old", key_fingerprint="old")
        self.exists = False
        self.deleted = False
        self.items = [Article(uuid.uuid4(), self.seller_id, "123", "SKU-1", "Товар")]

    async def list_sellers(self) -> list[Seller]:
        if self.deleted:
            return []
        return [
            Seller(
                self.seller_id,
                self.model.name,
                len(self.items),
                self.model.catalog_sync_status,
                None,
                self.model.catalog_sync_error,
            )
        ]

    async def fingerprint_exists(self, fingerprint: str, *, excluding=None) -> bool:
        return self.exists

    async def create(self, name: str, encrypted_key: str, fingerprint: str):
        self.model.name = name
        self.credential.encrypted_api_key = encrypted_key
        self.credential.key_fingerprint = fingerprint
        return self.model

    async def get(self, seller_id: uuid.UUID):
        return None if self.deleted or seller_id != self.seller_id else self.model

    async def get_credential(self, seller_id: uuid.UUID):
        return self.credential if seller_id == self.seller_id else None

    async def delete(self, seller_id: uuid.UUID) -> bool:
        if seller_id != self.seller_id:
            return False
        self.deleted = True
        return True

    async def list_articles(self, seller_id: uuid.UUID) -> list[Article]:
        return self.items


def service_fixture() -> tuple[SellerService, FakeSellerRepository, FakeSession]:
    session = FakeSession()
    repository = FakeSellerRepository()
    cipher = CredentialCipher((Fernet.generate_key().decode(),), "test-fingerprint-key")
    service = SellerService(cast(AsyncSession, session), cast(SellerRepository, repository), cipher)
    return service, repository, session


async def test_add_seller_creates_sync_outbox_event() -> None:
    service, _, session = service_fixture()

    created = await service.create("ООО Ромашка", "wb-api-key-123456")
    assert created.name == "ООО Ромашка"
    assert created.catalog_sync_status == "queued"
    assert any(isinstance(item, OutboxEventModel) for item in session.added)


async def test_list_sellers() -> None:
    service, repository, _ = service_fixture()
    repository.model.name = "ООО Ромашка"

    listed = await service.list_sellers()
    assert listed[0].name == "ООО Ромашка"


async def test_edit_seller() -> None:
    service, repository, _ = service_fixture()

    updated = await service.update(repository.seller_id, "Seller Latin", None)
    assert updated.name == "Seller Latin"


async def test_list_seller_articles() -> None:
    service, repository, _ = service_fixture()

    articles = await service.articles(repository.seller_id)
    assert articles[0].article == "123"


async def test_delete_seller() -> None:
    service, repository, _ = service_fixture()

    await service.delete(repository.seller_id)
    assert await service.list_sellers() == []
