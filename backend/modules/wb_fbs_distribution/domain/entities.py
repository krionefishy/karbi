import uuid
from dataclasses import dataclass
from datetime import datetime

# Пока автоматизация не пишет в WB, она считает план и показывает его оператору.
# Режим хранится на селлере, а не в конфигурации: пилот идёт на одном кабинете,
# пока остальные ещё считают вхолостую.
MODE_DRY_RUN = "dry_run"
MODE_WRITE = "write"


@dataclass(frozen=True, slots=True)
class SellerEnrollment:
    """Подключение селлера к автоматизации и право писать в его кабинет."""

    seller_id: uuid.UUID
    enrolled_at: datetime
    write_enabled: bool

    @property
    def mode(self) -> str:
        return MODE_WRITE if self.write_enabled else MODE_DRY_RUN
