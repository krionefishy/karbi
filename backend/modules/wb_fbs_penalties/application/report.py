import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from backend.modules.wb_fbs_penalties.application.view import TRACE_NO_ORDER, TRACE_NO_SUPPLY, PenaltiesView

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MOSCOW = ZoneInfo("Europe/Moscow")

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)
TOTAL_FONT = Font(bold=True)
MUTED_FONT = Font(color="808080")

# Её колонки — первыми и в её порядке; дальше то, что она заполняла руками.
COLUMNS: tuple[tuple[str, int], ...] = (
    ("Кабинет", 16),
    ("Неделя отчёта", 22),
    ("Баркод", 16),
    ("Артикул WB", 13),
    ("Название", 34),
    ("Вид удержания", 34),
    ("Группа", 20),
    ("Сумма, ₽", 12),
    ("Стикер МП", 15),
    ("Номер заказа (srid)", 40),
    ("Сборочное задание", 16),
    ("Дата заказа", 17),
    ("Склад продавца (отгрузил)", 26),
    ("Поставка", 18),
    ("Поставка создана", 17),
    ("QR отсканирован", 17),
    ("Склад WB назначения", 26),
)
TEXT_COLUMNS = {3, 9, 10, 11}


@dataclass(frozen=True, slots=True)
class PenaltiesReportFile:
    seller_name: str
    date_from: date
    date_to: date
    content: bytes

    @property
    def filename(self) -> str:
        name = re.sub(r"[^\w-]+", "_", self.seller_name, flags=re.UNICODE).strip("_") or "seller"
        return f"fbs_penalties_{name}_{self.date_from.isoformat()}_{self.date_to.isoformat()}.xlsx"


def render_workbook(view: PenaltiesView) -> bytes:
    """Одна книга на кабинет: итоги по группам сверху, дальше строки удержаний."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Штрафы"
    row_index = 1
    sheet.cell(
        row=row_index,
        column=1,
        value=f"{view.seller_name}: удержания {view.date_from:%d.%m.%Y} — {view.date_to:%d.%m.%Y}",
    ).font = TOTAL_FONT
    for total in view.totals:
        row_index += 1
        sheet.cell(row=row_index, column=1, value=total.title)
        sheet.cell(row=row_index, column=2, value=total.count)
        amount = sheet.cell(row=row_index, column=3, value=total.amount)
        amount.number_format = "#,##0.00"
    row_index += 2
    header_row = row_index
    for column, (title, width) in enumerate(COLUMNS, start=1):
        cell = sheet.cell(row=header_row, column=column, value=title)
        cell.fill, cell.font, cell.alignment = HEADER_FILL, HEADER_FONT, HEADER_ALIGNMENT
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.row_dimensions[header_row].height = 32
    sheet.freeze_panes = sheet.cell(row=header_row + 1, column=4)

    for item in view.rows:
        row_index += 1
        row = item.row
        values = [
            view.seller_name,
            f"{row.date_from:%d.%m.%Y} — {row.date_to:%d.%m.%Y}",
            row.barcode,
            row.nm_id or None,
            row.sa_name,
            row.bonus_type_name,
            item.group_title,
            row.amount,
            str(row.sticker_id) if row.sticker_id else "",
            row.srid,
            str(row.assembly_id) if row.assembly_id else "",
            _stamp(item.order_created_at or row.order_dt),
            item.warehouse_name or ("задание не найдено" if item.trace == TRACE_NO_ORDER else ""),
            item.supply_id or ("без поставки" if item.trace == TRACE_NO_SUPPLY else ""),
            _stamp(item.supply_created_at),
            _stamp(item.supply_scan_dt),
            item.destination_office_name or "",
        ]
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=row_index, column=column, value=value)
            if column in TEXT_COLUMNS:
                # Стикер и номера — текстом: иначе Excel покажет 5,74E+10.
                cell.number_format = "@"
            if column == 8:
                cell.number_format = "#,##0.00"
        if item.trace != "found":
            sheet.cell(row=row_index, column=13).font = MUTED_FONT
            sheet.cell(row=row_index, column=14).font = MUTED_FONT
    sheet.auto_filter.ref = f"A{header_row}:{get_column_letter(len(COLUMNS))}{max(row_index, header_row)}"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _stamp(moment: datetime | None) -> str:
    return moment.astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M") if moment else ""
