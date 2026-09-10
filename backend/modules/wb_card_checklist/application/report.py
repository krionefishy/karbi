import io
import re
from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from backend.modules.wb_card_checklist.application.view import ChecklistView
from backend.modules.wb_card_checklist.domain import ITEMS

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

IDENTITY_HEADERS = ("Дата заведения", "Артикул WB", "Артикул продавца", "Баркод", "Наименование товара", "ИП")
IDENTITY_WIDTHS = (14, 13, 22, 16, 36, 20)
FIRST_ITEM_COLUMN = len(IDENTITY_HEADERS) + 1
LAST_ITEM_COLUMN = FIRST_ITEM_COLUMN + len(ITEMS) - 1
DONE_COLUMN = LAST_ITEM_COLUMN + 1
STATUS_COLUMN = DONE_COLUMN + 1
COMMENT_COLUMN = STATUS_COLUMN + 1

# Цвета шапки — из таблицы, которую менеджеры вели руками: тёмная для
# реквизитов, синяя для пунктов проверки.
IDENTITY_FILL = PatternFill("solid", fgColor="1F3864")
ITEM_FILL = PatternFill("solid", fgColor="2E5F8A")
HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)
CENTERED = Alignment(horizontal="center", vertical="center", wrap_text=True)

# Выполнено — зелёный, нет — красный, как в интерфейсе. Ячейку без данных
# не красим: «не знаем» не должно выглядеть как «не выполнено».
DONE_FILL = PatternFill("solid", fgColor="C6EFCE")
DONE_FONT = Font(color="006100")
MISSING_FILL = PatternFill("solid", fgColor="FFC7CE")
MISSING_FONT = Font(color="9C0006")

READY = "ГОТОВ"
NOT_READY = "НЕ ГОТОВ"


@dataclass(frozen=True, slots=True)
class ChecklistReportFile:
    seller_name: str
    day: date
    content: bytes

    @property
    def filename(self) -> str:
        slug = re.sub(r"[^\w-]+", "-", self.seller_name, flags=re.UNICODE).strip("-") or "seller"
        return f"checklist_{slug}_{self.day.isoformat()}.xlsx"


def render_workbook(view: ChecklistView, timezone: ZoneInfo) -> bytes:
    """The checklist as a spreadsheet: identity columns, then what WB shows.

    Each item cell holds WB's own figure («12 фото», «22/25») and its colour
    says whether the item is done. Excel cannot count by colour, so «готово»
    and the status are written as values, not formulas.
    """
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Чек-лист"
    total = len(ITEMS)

    headers = [*IDENTITY_HEADERS, *(item.title for item in ITEMS), f"Готово из {total}", "Статус", "Комментарий"]
    sheet.append(headers)
    for column, _ in enumerate(headers, start=1):
        cell = sheet.cell(row=1, column=column)
        cell.fill = ITEM_FILL if FIRST_ITEM_COLUMN <= column <= LAST_ITEM_COLUMN else IDENTITY_FILL
        cell.font = HEADER_FONT
        cell.alignment = HEADER_ALIGNMENT
    sheet.row_dimensions[1].height = 42

    for index, row in enumerate(view.rows, start=2):
        created = row.card_created_at.astimezone(timezone).date() if row.card_created_at else None
        sheet.append(
            [
                created,
                row.article,
                row.vendor_code,
                row.barcode,
                row.title,
                view.seller_name,
                *(item.detail for item in row.items),
                row.done,
                READY if row.ready else NOT_READY,
                row.comment or None,
            ]
        )
        sheet.cell(row=index, column=1).number_format = "DD.MM.YYYY"
        # Артикул и баркод — текст: иначе Excel покажет баркод как 2,05E+12.
        sheet.cell(row=index, column=2).number_format = "@"
        sheet.cell(row=index, column=4).number_format = "@"
        for column in range(FIRST_ITEM_COLUMN, STATUS_COLUMN + 1):
            sheet.cell(row=index, column=column).alignment = CENTERED
        for offset, item in enumerate(row.items):
            _paint(sheet.cell(row=index, column=FIRST_ITEM_COLUMN + offset), item.done)
        status = sheet.cell(row=index, column=STATUS_COLUMN)
        _paint(status, row.ready)
        status.font = Font(bold=True, color=status.font.color.rgb if status.font.color else None)

    for column, width in enumerate(IDENTITY_WIDTHS, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    for column in range(FIRST_ITEM_COLUMN, LAST_ITEM_COLUMN + 1):
        sheet.column_dimensions[get_column_letter(column)].width = 15
    sheet.column_dimensions[get_column_letter(DONE_COLUMN)].width = 12
    sheet.column_dimensions[get_column_letter(STATUS_COLUMN)].width = 13
    sheet.column_dimensions[get_column_letter(COMMENT_COLUMN)].width = 36
    sheet.freeze_panes = sheet.cell(row=2, column=FIRST_ITEM_COLUMN)

    _instruction(workbook.create_sheet("Инструкция"), view.min_stock)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _paint(cell, done: bool | None) -> None:
    if done is None:
        return
    cell.fill = DONE_FILL if done else MISSING_FILL
    cell.font = DONE_FONT if done else MISSING_FONT


def _instruction(sheet, min_stock: int) -> None:
    bold = Font(bold=True)
    wrap = Alignment(wrap_text=True, vertical="top")
    total = len(ITEMS)
    rows: list[tuple[str, str]] = [
        ("Чек-лист карточки товара — выгрузка из сервиса", ""),
        ("", ""),
        ("Какие товары в таблице", f"Все карточки кабинета с остатком от {min_stock} шт. (WB и свои склады)."),
        ("Откуда данные", "Всё берётся из данных WB, руками ничего не отмечается."),
        ("Цвет ячейки", "Зелёный — пункт выполнен, красный — нет. В ячейке — то, что видит WB: «12 фото», «22/25»."),
        ("Ячейка без цвета", "У WB нет данных по пункту: это «не знаем», а не «не выполнено»."),
        (f"Колонка «Готово из {total}»", f"Сколько пунктов зелёные. «ГОТОВ» — когда зелёные все {total}."),
        ("", ""),
        ("Пункт", "Что означает"),
    ]
    rows.extend((item.title, item.meaning) for item in ITEMS)
    for values in rows:
        sheet.append(list(values))
    sheet["A1"].font = Font(bold=True, size=13)
    for row in (3, 4, 5, 6, 7, 9):
        sheet.cell(row=row, column=1).font = bold
    sheet.cell(row=9, column=2).font = bold
    for line in sheet.iter_rows(min_row=3):
        for cell in line:
            cell.alignment = wrap
    sheet.column_dimensions["A"].width = 30
    sheet.column_dimensions["B"].width = 90
