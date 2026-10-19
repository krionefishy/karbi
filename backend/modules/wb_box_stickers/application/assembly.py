import re

from backend.modules.wb_box_stickers.domain import Assembly, AssemblyRow

SUPPLY_RE = re.compile(r"^\d{8}$")
BARCODE_RE = re.compile(r"^\d{13}$")
INTEGER_RE = re.compile(r"^\d{1,6}$")
SCHEME_RE = re.compile(r"^\d{1,4}\s*[-–]\s*\d{1,5}(\s*\+\s*\d{1,4}\s*[-–]\s*\d{1,5})*$")
DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.\d{2,4})?$")
PROBLEMS_LIMIT = 20


def _cells(line: str) -> list[str]:
    """Ячейки строки: табуляция из Google-таблицы, иначе — два и больше пробелов."""
    parts = line.split("\t") if "\t" in line else re.split(r"\s{2,}", line)
    return [part.strip() for part in parts]


def _scheme_text(cell: str) -> str | None:
    """Текст схемы, если ячейка на неё похожа: «3-60+1-40» или дата, в которую превратилось «1-4»."""
    date = DATE_RE.match(cell)
    if date:
        return f"{int(date.group(1))}-{int(date.group(2))}"
    if SCHEME_RE.match(cell):
        return cell
    return None


def parse_scheme(text: str) -> tuple[int, ...]:
    """«3-60+1-40» → (60, 60, 60, 40): количество в каждом коробе по порядку."""
    boxes: list[int] = []
    for part in re.split(r"\s*\+\s*", text.strip()):
        count, quantity = (int(piece) for piece in re.split(r"\s*[-–]\s*", part))
        if count < 1 or quantity < 1:
            raise ValueError(part)
        boxes.extend([quantity] * count)
    return tuple(boxes)


def _parse_line(number: int, line: str) -> AssemblyRow | None:
    cells = _cells(line)
    barcode_at = next((index for index, cell in enumerate(cells) if BARCODE_RE.match(cell)), None)
    if barcode_at is None:
        return None
    supply_id = next((int(cell) for cell in cells[:barcode_at] if SUPPLY_RE.match(cell)), None)

    scheme = ""
    quantity: int | None = None
    for index in range(len(cells) - 1, barcode_at, -1):
        text = _scheme_text(cells[index])
        if text is None:
            continue
        numbers = [int(cell) for cell in cells[barcode_at + 1 : index] if INTEGER_RE.match(cell)]
        if not numbers:
            continue
        scheme, quantity = text, numbers[-1]
        break
    if not scheme:
        numbers = [int(cell) for cell in cells[barcode_at + 1 :] if INTEGER_RE.match(cell)]
        quantity = numbers[-1] if numbers else None
    try:
        boxes = parse_scheme(scheme) if scheme else ()
    except ValueError:
        boxes = ()
    return AssemblyRow(
        line=number,
        supply_id=supply_id,
        barcode=cells[barcode_at],
        quantity=quantity,
        scheme=scheme,
        boxes=boxes,
    )


def parse_assembly(text: str) -> Assembly:
    """Строки сборки одной поставки по порядку вставки и всё, из-за чего раскладывать нельзя.

    Строка без номера поставки, когда у остальных он есть, — не из этой поставки
    («не везем» в таблице пишут именно так) и пропускается.
    """
    rows = [row for row in (_parse_line(number, line) for number, line in enumerate(text.splitlines(), 1)) if row]
    problems: list[str] = []
    supply_ids = sorted({row.supply_id for row in rows if row.supply_id})
    if len(supply_ids) > 1:
        problems.append(
            "Вставлены строки разных поставок: " + ", ".join(str(item) for item in supply_ids) + " — нужна одна"
        )
    supply_id = supply_ids[0] if len(supply_ids) == 1 else None
    if supply_id:
        rows = [row for row in rows if row.supply_id]
    if not rows:
        problems.append("В вставленном тексте нет строк с баркодом — скопируйте строки поставки из таблицы сборки")
        return Assembly(supply_id=None, rows=(), problems=tuple(problems))
    if not supply_id and not problems:
        problems.append("В строках нет номера поставки — в таблице он стоит в первой колонке")

    unparsed: list[str] = []
    mismatched: list[str] = []
    for row in rows:
        if not row.boxes:
            unparsed.append(f"строка {row.line} (баркод {row.barcode})")
        elif row.quantity is not None and sum(row.boxes) != row.quantity:
            mismatched.append(
                f"строка {row.line}: по схеме «{row.scheme}» {sum(row.boxes)} шт, а в сборке {row.quantity} шт"
            )
    if unparsed:
        problems.append("Нет схемы коробов (вида «3-60+1-40») в колонке «Сборка склад»: " + _listed(unparsed))
    if mismatched:
        problems.append("Схема не сходится с количеством: " + _listed(mismatched))
    seen: dict[str, int] = {}
    for row in rows:
        if row.barcode in seen:
            problems.append(f"Баркод {row.barcode} в строках {seen[row.barcode]} и {row.line} — один товар дважды")
        seen.setdefault(row.barcode, row.line)
    return Assembly(supply_id=supply_id, rows=tuple(rows), problems=tuple(problems))


def _listed(items: list[str]) -> str:
    shown = "; ".join(items[:PROBLEMS_LIMIT])
    return shown + (f"; и ещё {len(items) - PROBLEMS_LIMIT}" if len(items) > PROBLEMS_LIMIT else "")
