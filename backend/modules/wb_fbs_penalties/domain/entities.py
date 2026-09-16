from dataclasses import dataclass
from datetime import date, datetime

# Группы удержаний фин. отчёта. Одна строка — одна группа: первая, у которой
# есть ненулевая сумма. Штрафы отдельно от остального, потому что оспаривают
# именно их; остальное — чтобы видеть разницу, а не смешивать в одну сумму.
GROUP_PENALTIES = "penalties"
GROUP_DEDUCTIONS = "deductions"
GROUP_LOGISTICS = "logistics"
GROUP_STORAGE = "storage"
GROUPS = (GROUP_PENALTIES, GROUP_DEDUCTIONS, GROUP_LOGISTICS, GROUP_STORAGE)
GROUP_TITLES = {
    GROUP_PENALTIES: "Штрафы",
    GROUP_DEDUCTIONS: "Удержания и доплаты",
    GROUP_LOGISTICS: "Логистика (перевыставление)",
    GROUP_STORAGE: "Хранение и приёмка",
}


@dataclass(frozen=True, slots=True)
class ReportRow:
    """Строка детализации отчёта реализации WB — как отдал API, без пересчёта.

    `sticker_id` — «стикер МП» в таблице селлера (`shk_id` отчёта); `srid` и
    `assembly_id` — ключи, которыми строка сходится со сборочным заданием.
    """

    rrd_id: int
    realizationreport_id: int
    date_from: date
    date_to: date
    create_dt: date | None
    srid: str
    assembly_id: int | None
    sticker_id: int | None
    order_dt: datetime | None
    sale_dt: datetime | None
    rr_dt: date | None
    nm_id: int
    sa_name: str
    subject_name: str
    barcode: str
    ts_name: str
    bonus_type_name: str
    supplier_oper_name: str
    delivery_method: str
    office_name: str
    penalty: float
    deduction: float
    rebill_logistic_cost: float
    storage_fee: float
    additional_payment: float
    acceptance: float

    @property
    def group(self) -> str | None:
        if self.penalty:
            return GROUP_PENALTIES
        if self.deduction or self.additional_payment:
            return GROUP_DEDUCTIONS
        if self.rebill_logistic_cost:
            return GROUP_LOGISTICS
        if self.storage_fee or self.acceptance:
            return GROUP_STORAGE
        return None

    @property
    def amount(self) -> float:
        """Сумма той группы, куда строка попала: остальные поля у неё нулевые или чужие."""
        group = self.group
        if group == GROUP_PENALTIES:
            return self.penalty
        if group == GROUP_DEDUCTIONS:
            return self.deduction + self.additional_payment
        if group == GROUP_LOGISTICS:
            return self.rebill_logistic_cost
        if group == GROUP_STORAGE:
            return self.storage_fee + self.acceptance
        return 0.0

    @property
    def charged(self) -> bool:
        return self.group is not None
