from backend.modules.notifications.application import templates


def test_digest_is_code_and_addresses_with_counts_only() -> None:
    text = templates.render(
        templates.RETURNS_DIGEST,
        {
            "seller_name": "Байбурин",
            "date": "2026-09-25",
            "code": "21588",
            "qr": "WB|x",
            "ready": [
                {"address": "посёлок Развилка, Римский проезд 15", "count": 3},
                {"address": "Дзержинский", "count": 6},
            ],
            "ready_total": 9,
            "transit": [{"address": "Дзержинский", "count": 2}],
            "transit_total": 2,
            "overdue_total": 4,
            "free_days": 3,
            "alerts": ["Слетел вход на wildberries.ru (Chrome): войдите заново под телефоном владельца"],
        },
    )
    assert text == (
        "Байбурин — код для получения доставки: 21588\n\n"
        "Готово к выдаче: 9 шт.\n"
        "• посёлок Развилка, Римский проезд 15 — 3 шт.\n"
        "• Дзержинский — 6 шт.\n\n"
        "Едет в ПВЗ: 2 шт.\n"
        "• Дзержинский — 2 шт.\n\n"
        "Лежат дольше 3 дн.: 4 шт. — хранение платное\n"
        "Слетел вход на wildberries.ru (Chrome): войдите заново под телефоном владельца"
    )
    assert "http" not in text and "стикер" not in text
    # Без кода — одна строка почему, без расширения — куда идти.
    assert (
        templates.render(templates.RETURNS_DIGEST, {"seller_name": "М", "ready_total": 0}) == "М — кода на сегодня нет"
    )
    assert "расширение не подключено (/extension)" in templates.render(
        templates.RETURNS_DIGEST, {"seller_name": "М", "no_install": True}
    )


def test_command_replies_render() -> None:
    welcome = templates.render(templates.RETURNS_WELCOME, {"sellers": ["Байбурин"]})
    assert welcome.startswith("Буду присылать возвраты по Байбурин")
    assert "/extension" in welcome and "/qr" in welcome and "9:00, 12:00, 15:00 и 18:00" in welcome

    listing = templates.render(
        templates.RETURNS_LIST,
        {
            "sellers": [
                {
                    "name": "Байбурин",
                    "ready_total": 1,
                    "transit_total": 2,
                    "ready": [{"address": "Развилка 52к1", "count": 1}],
                    "transit": [{"address": "Дзержинский", "count": 2}],
                },
                {"name": "Пусто", "ready_total": 0, "transit_total": 0, "ready": [], "transit": []},
            ]
        },
    )
    assert listing == (
        "Байбурин\nГотово к выдаче: 1 шт.\n• Развилка 52к1 — 1 шт.\nЕдет в ПВЗ: 2 шт.\n• Дзержинский — 2 шт.\n\n"
        "Пусто\nАктивных возвратов нет."
    )
    assert templates.render(templates.RETURNS_LIST, {"sellers": []}) == "Активных возвратов нет."

    code = templates.render(
        templates.RETURNS_CODE,
        {
            "name": "А",
            "date": "2026-09-22",
            "code": "21588",
            "qr": "WB|x",
            "qr_url": "https://x/q.png",
            "offices": [{"address": "Развилка, Римский проезд 15", "count": 3}, {"address": "Дзержинский", "count": 6}],
        },
    )
    assert code == (
        "А — код для получения доставки: 21588\n\n"
        "Готово к выдаче: 9 шт.\n• Развилка, Римский проезд 15 — 3 шт.\n• Дзержинский — 6 шт."
    )
    assert "Б: расширение не подключено" in templates.render(
        templates.RETURNS_CODE, {"name": "Б", "date": "2026-09-22", "code": None, "no_install": True}
    )
    assert "В: кода на сегодня ещё нет" in templates.render(
        templates.RETURNS_CODE, {"name": "В", "date": "2026-09-22", "code": None, "requested": True}
    )
    assert "/returns" in templates.render(templates.RETURNS_UNKNOWN, {"command": "/x"})


def test_qr_attachment_is_rendered_only_when_the_string_is_there() -> None:
    photo = templates.render_attachment(
        templates.RETURNS_CODE, {"name": "А", "date": "2026-09-22", "code": "412", "qr": "WB|412"}
    )
    assert photo is not None and photo["kind"] == "photo" and photo["caption"] == "А — код для получения доставки: 412"
    assert photo["png_base64"].startswith("iVBORw0KGgo")
    assert templates.render_attachment(templates.RETURNS_CODE, {"name": "А", "code": None}) is None
    assert templates.render_attachment(templates.RETURNS_HELP, {}) is None
    digest = templates.render_attachment(templates.RETURNS_DIGEST, {"seller_name": "М", "code": "1", "qr": "WB|1"})
    assert digest is not None and digest["caption"] == "М — код для получения доставки: 1"

    extension = templates.render(
        templates.RETURNS_EXTENSION,
        {"download_url": "https://x/ext.zip", "ttl_minutes": 15, "codes": [{"name": "М", "code": "123456"}]},
    )
    assert "https://x/ext.zip" in extension and "М: 123456" in extension
