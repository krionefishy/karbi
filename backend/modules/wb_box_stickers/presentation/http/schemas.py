from pydantic import BaseModel


class ItemResponse(BaseModel):
    barcode: str
    quantity: int
    vendor_code: str
    tech_size: str


class BoxResponse(BaseModel):
    position: int
    shk: str
    package_code: str
    page: int
    quantity: int
    items: list[ItemResponse]


class PlanResponse(BaseModel):
    supply_id: int | None
    seller_id: str | None
    seller_name: str
    ready: bool
    problems: list[str]
    boxes: list[BoxResponse]
