from backend.modules.wb_core.application import OrderTrace
from backend.modules.wb_fbs_penalties.domain import TRACE_FOUND, TRACE_NO_SUPPLY, ReportRow, RowTrace


def row_trace(trace: OrderTrace) -> RowTrace:
    """Что из задания и поставки зеркала ложится на строку отчёта."""
    order, supply = trace.order, trace.supply
    return RowTrace(
        trace=TRACE_FOUND if supply else TRACE_NO_SUPPLY,
        warehouse_id=order.warehouse_id,
        warehouse_name=trace.warehouse_name or f"склад {order.warehouse_id}",
        order_created_at=order.created_at,
        supply_id=order.supply_id,
        supply_created_at=supply.created_at if supply else None,
        supply_scan_dt=supply.scan_dt if supply else None,
        destination_office_name=trace.destination_office_name,
    )


def row_keys(row: ReportRow) -> list[str]:
    """Ключи, по которым строка отчёта ищется среди заданий: srid, номер задания, стикер."""
    return [key for key in (row.srid, str(row.assembly_id or ""), str(row.sticker_id or "")) if key]
