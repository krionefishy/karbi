import uuid
from abc import ABC, abstractmethod
from typing import Protocol


class AutomationEnrollment(ABC):
    """One automation's answer to "who is connected to me".

    The registry owns sellers but must never know which tables an automation
    keeps — it used to, through a raw cross-schema DELETE, and every new module
    would have had to be added to that statement by hand. Automations implement
    this port instead and register the implementation in the DI provider.
    """

    automation_id: str
    title: str

    @abstractmethod
    async def seller_ids(self) -> set[uuid.UUID]:
        """Sellers currently enrolled in this automation."""

    @abstractmethod
    async def attach(self, seller_id: uuid.UUID) -> None:
        """Enroll the seller; enrolling twice must not fail."""

    @abstractmethod
    async def detach(self, seller_id: uuid.UUID) -> None:
        """Stop collecting for the seller and keep everything already collected."""

    @abstractmethod
    async def purge(self, seller_id: uuid.UUID) -> None:
        """Erase what this automation stores about the seller, history included."""


class TrackingRepository(Protocol):
    """То, что нужно базовому подключению от хранилища автоматизации."""

    async def tracked_seller_ids(self) -> set[uuid.UUID]: ...

    async def track(self, seller_id: uuid.UUID) -> None: ...

    async def untrack(self, seller_id: uuid.UUID) -> None: ...

    async def purge_seller(self, seller_id: uuid.UUID) -> None: ...


class TrackedEnrollment[R: TrackingRepository](AutomationEnrollment):
    """Подключение, которое целиком делегирует хранилищу автоматизации.

    Наследнику остаётся назвать автоматизацию (`automation_id`, `title`); метод переопределяют
    только там, где поведение отличается, например когда своей истории у модуля нет.
    """

    def __init__(self, repository: R) -> None:
        self.repository = repository

    async def seller_ids(self) -> set[uuid.UUID]:
        return await self.repository.tracked_seller_ids()

    async def attach(self, seller_id: uuid.UUID) -> None:
        await self.repository.track(seller_id)

    async def detach(self, seller_id: uuid.UUID) -> None:
        await self.repository.untrack(seller_id)

    async def purge(self, seller_id: uuid.UUID) -> None:
        await self.repository.purge_seller(seller_id)
