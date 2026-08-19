import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import (
    AutomationEnrollment,
    AutomationNotFoundError,
    SellerArchivedError,
    SellerNotFoundError,
    SellerService,
)
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


class FakeEnrollment(AutomationEnrollment):
    automation_id = "wb-reviews"
    title = "Мониторинг отзывов Wildberries"

    def __init__(self) -> None:
        self.enrolled: set[uuid.UUID] = set()
        self.purged: list[uuid.UUID] = []

    async def seller_ids(self) -> set[uuid.UUID]:
        return set(self.enrolled)

    async def attach(self, seller_id: uuid.UUID) -> None:
        self.enrolled.add(seller_id)

    async def detach(self, seller_id: uuid.UUID) -> None:
        self.enrolled.discard(seller_id)

    async def purge(self, seller_id: uuid.UUID) -> None:
        self.purged.append(seller_id)
        self.enrolled.discard(seller_id)


class FakeSellerRepository:
    def __init__(self) -> None:
        self.seller_id = uuid.uuid4()
        self.model = SimpleNamespace(
            id=self.seller_id,
            name="Селлер",
            catalog_sync_status="success",
            catalog_sync_error=None,
            archived_at=None,
        )
        self.credential = SimpleNamespace(encrypted_api_key="old", key_fingerprint="old")
        self.exists = False
        self.deleted = False
        self.items = [Article(uuid.uuid4(), self.seller_id, "123", "SKU-1", "Товар")]

    async def list_sellers(self, *, include_archived: bool = False) -> list[Seller]:
        if self.deleted:
            return []
        if self.model.archived_at is not None and not include_archived:
            return []
        return [
            Seller(
                self.seller_id,
                self.model.name,
                len(self.items),
                self.model.catalog_sync_status,
                None,
                self.model.catalog_sync_error,
                self.model.archived_at,
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

    async def archive(self, seller_id: uuid.UUID) -> bool:
        if seller_id != self.seller_id or self.model.archived_at is not None:
            return False
        self.model.archived_at = datetime.now(UTC)
        return True

    async def restore(self, seller_id: uuid.UUID, encrypted_key: str, fingerprint: str) -> bool:
        if seller_id != self.seller_id or self.model.archived_at is None:
            return False
        self.model.archived_at = None
        self.model.catalog_sync_status = "queued"
        self.model.catalog_sync_error = None
        self.credential.encrypted_api_key = encrypted_key
        self.credential.key_fingerprint = fingerprint
        return True

    async def delete(self, seller_id: uuid.UUID) -> bool:
        if seller_id != self.seller_id:
            return False
        self.deleted = True
        return True

    async def list_articles(self, seller_id: uuid.UUID) -> list[Article]:
        return self.items


def service_fixture() -> tuple[SellerService, FakeSellerRepository, FakeSession, FakeEnrollment]:
    session = FakeSession()
    repository = FakeSellerRepository()
    enrollment = FakeEnrollment()
    cipher = CredentialCipher((Fernet.generate_key().decode(),), "test-fingerprint-key")
    service = SellerService(cast(AsyncSession, session), cast(SellerRepository, repository), cipher, [enrollment])
    return service, repository, session, enrollment


async def test_add_seller_creates_sync_outbox_event() -> None:
    service, _, session, _ = service_fixture()

    created = await service.create("ООО Ромашка", "wb-api-key-123456")
    assert created.name == "ООО Ромашка"
    assert created.catalog_sync_status == "queued"
    assert any(isinstance(item, OutboxEventModel) for item in session.added)


async def test_list_sellers() -> None:
    service, repository, _, _ = service_fixture()
    repository.model.name = "ООО Ромашка"

    listed = await service.list_sellers()
    assert listed[0].name == "ООО Ромашка"


async def test_edit_seller() -> None:
    service, repository, _, _ = service_fixture()

    updated = await service.update(repository.seller_id, "Seller Latin", None)
    assert updated.name == "Seller Latin"


async def test_list_seller_articles() -> None:
    service, repository, _, _ = service_fixture()

    articles = await service.articles(repository.seller_id)
    assert articles[0].article == "123"


async def test_a_seller_is_created_outside_automations() -> None:
    service, repository, _, enrollment = service_fixture()

    await service.create("ООО Ромашка", "wb-api-key-123456")
    assert enrollment.enrolled == set()
    assert await service.enrolled("wb-reviews") == []

    enrolled = await service.enroll("wb-reviews", repository.seller_id)
    assert enrolled.id == repository.seller_id
    assert [seller.id for seller in await service.enrolled("wb-reviews")] == [repository.seller_id]


async def test_leaving_an_automation_keeps_the_seller_and_his_data() -> None:
    service, repository, _, enrollment = service_fixture()
    await service.enroll("wb-reviews", repository.seller_id)

    await service.unenroll("wb-reviews", repository.seller_id)

    assert enrollment.enrolled == set()
    assert enrollment.purged == []
    assert [seller.id for seller in await service.list_sellers()] == [repository.seller_id]


async def test_archiving_detaches_from_every_automation_but_keeps_history() -> None:
    service, repository, _, enrollment = service_fixture()
    await service.enroll("wb-reviews", repository.seller_id)

    archived = await service.archive(repository.seller_id)

    assert archived.is_archived
    assert enrollment.enrolled == set()
    assert enrollment.purged == []
    assert await service.list_sellers() == []
    assert [seller.id for seller in await service.list_sellers(include_archived=True)] == [repository.seller_id]


async def test_an_archived_seller_is_not_collected_for() -> None:
    service, repository, _, _ = service_fixture()
    await service.archive(repository.seller_id)

    with pytest.raises(SellerArchivedError):
        await service.request_sync(repository.seller_id)
    with pytest.raises(SellerArchivedError):
        await service.enroll("wb-reviews", repository.seller_id)


async def test_restoring_asks_for_the_key_again() -> None:
    service, repository, _, _ = service_fixture()
    await service.archive(repository.seller_id)

    restored = await service.restore(repository.seller_id, "wb-api-key-restored")

    assert not restored.is_archived
    assert restored.catalog_sync_status == "queued"
    assert repository.credential.encrypted_api_key != "old"


async def test_purge_lets_every_automation_erase_its_own_data() -> None:
    service, repository, _, enrollment = service_fixture()
    await service.enroll("wb-reviews", repository.seller_id)

    await service.purge(repository.seller_id)

    assert enrollment.purged == [repository.seller_id]
    assert await service.list_sellers(include_archived=True) == []


async def test_membership_is_reported_per_seller() -> None:
    service, repository, _, _ = service_fixture()
    await service.enroll("wb-reviews", repository.seller_id)
    other = uuid.uuid4()

    membership = await service.automations_of([repository.seller_id, other])

    assert membership == {repository.seller_id: ["wb-reviews"], other: []}


async def test_unknown_automation_is_rejected() -> None:
    service, repository, _, _ = service_fixture()

    with pytest.raises(AutomationNotFoundError):
        await service.enroll("wb-turnover", repository.seller_id)


async def test_purging_an_unknown_seller_is_an_error() -> None:
    service, _, _, _ = service_fixture()

    with pytest.raises(SellerNotFoundError):
        await service.purge(uuid.uuid4())
