from datetime import UTC, datetime

import pytest

from backend.modules.wb_fbs_distribution.infrastructure.onec import SnapshotFormatError, parse_snapshot

CSV = (
    "item_id;barcode;name;characteristic;quantity;generated_at\n"
    "НФ-001;2000000000017;Футболка синяя;M;150;2026-08-26T09:00:00+03:00\n"
    "НФ-001;2000000000024;Футболка синяя;L;8;2026-08-26T09:00:00+03:00\n"
)


def parse(text: str):
    return parse_snapshot(text.encode("utf-8"))


def test_the_agreed_csv_is_read_as_it_was_agreed() -> None:
    snapshot = parse(CSV)

    assert len(snapshot.lines) == 2
    first = snapshot.lines[0]
    assert (first.item_id, first.barcode, first.characteristic, first.quantity) == (
        "НФ-001",
        "2000000000017",
        "M",
        150,
    )
    # Время приходит с московским смещением и должно остаться тем же моментом.
    assert snapshot.generated_at == datetime(2026, 8, 26, 6, 0, tzinfo=UTC)


def test_russian_headers_are_accepted_too() -> None:
    """The 1C export format is not agreed yet; refusing a file over a header is
    the worst way to find that out."""
    snapshot = parse("Код;Штрихкод;Наименование;Характеристика;Остаток\nA1;2000;Товар;;7\n")

    assert (snapshot.lines[0].item_id, snapshot.lines[0].quantity) == ("A1", 7)


def test_json_is_read_as_well_as_csv() -> None:
    snapshot = parse(
        '{"generated_at": "2026-08-26T06:00:00Z",'
        ' "lines": [{"item_id": "A1", "barcode": "2000", "name": "Товар", "quantity": 3}]}'
    )

    assert snapshot.generated_at == datetime(2026, 8, 26, 6, 0, tzinfo=UTC)
    assert snapshot.lines[0].quantity == 3


def test_a_bare_json_list_works_and_gets_a_receiving_time() -> None:
    snapshot = parse_snapshot(
        b'[{"item_id": "A1", "barcode": "2000", "quantity": 3}]',
        received_at=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
    )

    assert snapshot.generated_at == datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def test_windows_1251_is_decoded_because_that_is_what_1c_exports() -> None:
    snapshot = parse_snapshot("Код;Штрихкод;Остаток\nА1;2000;5\n".encode("cp1251"))

    assert (snapshot.lines[0].item_id, snapshot.lines[0].quantity) == ("А1", 5)


def test_a_quantity_with_a_comma_and_spaces_is_still_a_number() -> None:
    snapshot = parse("item_id;barcode;quantity\nA1;2000;1 200,00\n")

    assert snapshot.lines[0].quantity == 1200


def test_a_fractional_quantity_is_refused_rather_than_rounded() -> None:
    """Half a piece cannot be put on a shelf, and rounding it silently would
    promise WB stock that does not exist."""
    with pytest.raises(SnapshotFormatError, match="дробный"):
        parse("item_id;barcode;quantity\nA1;2000;1.5\n")


def test_a_line_without_a_quantity_is_refused() -> None:
    with pytest.raises(SnapshotFormatError, match="нет остатка"):
        parse("item_id;barcode;quantity\nA1;2000;\n")


def test_a_line_identified_by_nothing_is_refused() -> None:
    with pytest.raises(SnapshotFormatError):
        parse("item_id;barcode;quantity\n;;5\n")


def test_a_barcode_alone_can_identify_the_line() -> None:
    """Whether 1C has stable item ids is still unconfirmed; a barcode-only
    export must not be dead on arrival."""
    snapshot = parse("barcode;quantity\n2000000000017;5\n")

    assert snapshot.lines[0].item_id == "2000000000017"


def test_an_empty_file_says_so_instead_of_wiping_every_pool() -> None:
    with pytest.raises(SnapshotFormatError, match="пустой"):
        parse("")


def test_broken_json_names_the_problem() -> None:
    with pytest.raises(SnapshotFormatError, match="JSON"):
        parse('{"lines": [')
