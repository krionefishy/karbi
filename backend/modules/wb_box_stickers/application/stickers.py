"""PDF со стикерами коробов: прочитать, переставить и перепроверить.

Страницы WB не перерисовываются и ничем не дополняются — переставляются
целиком, поэтому стикер остаётся ровно тем, что выдал WB. Готовый файл
читается заново тем же разбором, что и исходный: не сошёлся хоть один
стикер — файл не отдаётся.
"""

import io
import re
from collections.abc import Sequence

import pypdfium2 as pdfium
import zxingcpp
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

from backend.modules.wb_box_stickers.domain import MatchedBox, StickerInputError, StickerPage

MAX_PAGES = 2000
# QR на стикере крупный: 150 dpi читаются без промахов и вдвое быстрее 300.
RENDER_SCALE = 150 / 72

SHK_LINE = re.compile(r"^[\d ]{5,}$")
QUANTITY = re.compile(r"Кол-во товаров:\s*(\d+)")
SUPPLY = re.compile(r"№ поставки:\s*(\d+)")
SELLER = re.compile(r"Продавец:\s*(.+)")


def _open(data: bytes) -> PdfReader:
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = len(reader.pages)
    except (PdfReadError, ValueError, OSError) as error:
        raise StickerInputError("PDF не открывается — нужен файл со стикерами коробов из кабинета WB") from error
    if pages == 0:
        raise StickerInputError("В PDF нет страниц")
    if pages > MAX_PAGES:
        raise StickerInputError(f"В PDF {pages} страниц — больше {MAX_PAGES} за раз не собираем")
    return reader


def _qr_codes(document: pdfium.PdfDocument, index: int) -> set[str]:
    page = document[index]
    try:
        # to_pil тянет Pillow лениво; явный тип держит её в зависимостях сервиса.
        image: Image.Image = page.render(scale=RENDER_SCALE, grayscale=True).to_pil()
    finally:
        page.close()
    found = zxingcpp.read_barcodes(image)
    return {item.text.strip() for item in found if item.format == zxingcpp.BarcodeFormat.QRCode and item.text.strip()}


def read_stickers(data: bytes) -> list[StickerPage]:
    """Каждая страница — один стикер: короткий номер и цифры текстом, полный код из QR."""
    reader = _open(data)
    document = pdfium.PdfDocument(data)
    try:
        pages: list[StickerPage] = []
        for index, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            shk = next((line.replace(" ", "") for line in lines[:3] if SHK_LINE.match(line)), "")
            codes = _qr_codes(document, index)
            # На стикере два QR с одним и тем же кодом; разные — это уже не стикер короба.
            if len(codes) > 1:
                raise StickerInputError(f"На странице {index + 1} PDF разные QR-коды — это не стикер короба WB")
            quantity = QUANTITY.search(text)
            supply = SUPPLY.search(text)
            seller = SELLER.search(text)
            pages.append(
                StickerPage(
                    index=index,
                    shk=shk,
                    package_code=next(iter(codes), ""),
                    quantity=int(quantity.group(1)) if quantity else None,
                    supply_id=int(supply.group(1)) if supply else None,
                    seller_name=seller.group(1).strip() if seller else "",
                )
            )
        return pages
    finally:
        document.close()


def build_stickers(data: bytes, boxes: Sequence[MatchedBox]) -> bytes:
    """Новый PDF: страницы исходника в порядке `boxes`, без изменений."""
    source = _open(data)
    writer = PdfWriter()
    for box in boxes:
        writer.add_page(source.pages[box.page_index])
    writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def verify_stickers(data: bytes, boxes: Sequence[MatchedBox]) -> list[str]:
    """Прочитать собранный файл как чужой и сверить стикер за стикером."""
    pages = read_stickers(data)
    if len(pages) != len(boxes):
        return [f"В собранном файле {len(pages)} страниц вместо {len(boxes)}"]
    problems = []
    for page, box in zip(pages, boxes, strict=True):
        if page.package_code != box.package_code:
            problems.append(f"Стикер № {box.position}: QR не читается или не тот короб")
        elif box.shk and page.shk != box.shk:
            problems.append(f"Стикер № {box.position}: номер {page.shk or '—'} вместо {box.shk}")
    return problems
