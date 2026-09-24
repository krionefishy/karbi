from backend.modules.notifications.application import templates


def test_ready_message_groups_by_office_and_names_deadlines() -> None:
    text = templates.render(
        templates.RETURNS_READY,
        {
            "seller_name": "Байбурин",
            "total": 2,
            "free_days": 3,
            "storage_days": 7,
            "offices": [
                {
                    "address": "Дзержинский, площадь Дмитрия Донского 6",
                    "count": 2,
                    "free_until": "2026-09-20",
                    "deadline": "2026-09-24",
                    "items": [
                        {
                            "title": "Шуруповерты · KARBI",
                            "sticker": "46685200489",
                            "return_type": "Возврат брака",
                            "reason": "",
                        },
                        {
                            "title": "Электрощетки · KARBI",
                            "sticker": "53509818349",
                            "return_type": "",
                            "reason": "Цвет",
                        },
                    ],
                }
            ],
            "code": "412",
            "code_date": "2026-09-22",
        },
    )

    assert text.startswith("Байбурин: 2 возврата готовы к выдаче")
    assert "ПВЗ Дзержинский, площадь Дмитрия Донского 6 — 2 шт." in text
    assert "• Шуруповерты · KARBI · стикер 46685200489\n  Возврат брака" in text
    assert "Бесплатно до 20 сент., забрать до 24 сент." in text
    assert "Код получения на 22 сент.: 412" in text


def test_claims_message_lists_photos_and_deadline() -> None:
    text = templates.render(
        templates.RETURNS_CLAIMS,
        {
            "seller_name": "Байбурин",
            "total": 1,
            "review_days": 5,
            "claims": [
                {
                    "name": "Бинокль",
                    "price": 2320,
                    "comment": "не как в описании",
                    "photos": ["https://claim-basket-01.wbbasket.ru/x/1.webp"],
                    "deadline": "2026-09-26T17:56:24+03:00",
                }
            ],
        },
    )

    assert "1 новая заявка на возврат" in text
    assert "• Бинокль · 2 320 ₽" in text
    assert "«не как в описании»" in text
    assert "фото: https://claim-basket-01.wbbasket.ru/x/1.webp" in text
    assert "ответить до 26 сент." in text


def test_reminder_wording_depends_on_day() -> None:
    base = {"seller_name": "Магазин", "total": 1, "free_days": 3, "storage_days": 7, "offices": []}
    assert "с завтрашнего дня хранение платное" in templates.render(templates.RETURNS_REMINDER, {**base, "day": 3})
    assert "через два дня товар уедет на утилизацию" in templates.render(templates.RETURNS_REMINDER, {**base, "day": 5})


def test_command_replies_render() -> None:
    welcome = templates.render(templates.RETURNS_WELCOME, {"sellers": ["Байбурин"]})
    assert welcome.startswith("Буду присылать возвраты по Байбурин")
    assert "/extension" in welcome and "/qr" in welcome

    listing = templates.render(
        templates.RETURNS_LIST,
        {
            "sellers": [
                {
                    "name": "Байбурин",
                    "ready_total": 1,
                    "transit_total": 2,
                    "open_claims": 3,
                    "offices": [
                        {
                            "address": "Развилка 52к1",
                            "count": 1,
                            "free_until": "2026-09-20",
                            "deadline": "2026-09-24",
                            "items": [{"title": "Шуруповерты", "sticker": "1", "return_type": "", "reason": ""}],
                        }
                    ],
                }
            ]
        },
    )
    assert listing.startswith("Байбурин: готово 1, едет 2, 3 открытые заявки")
    assert "ПВЗ Развилка 52к1 — 1 шт." in listing
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
    assert code.startswith("А — код для получения доставки: 21588\nДействует 22 сент.")
    assert "• Развилка, Римский проезд 15 — 3 шт." in code and "• Дзержинский — 6 шт." in code
    assert "https://x/q.png" not in code  # картинка идёт вложением, ссылка — только без неё
    assert "QR: https://x/q.png" in templates.render(
        templates.RETURNS_CODE, {"name": "А", "date": "2026-09-22", "code": "1", "qr_url": "https://x/q.png"}
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

    ready = templates.render(
        templates.RETURNS_READY,
        {
            "seller_name": "М",
            "total": 1,
            "offices": [],
            "code": "412",
            "code_date": "2026-09-22",
            "qr_url": "https://x/q.png",
        },
    )
    assert "Код получения на 22 сент.: 412\nQR: https://x/q.png" in ready
    extension = templates.render(
        templates.RETURNS_EXTENSION,
        {"download_url": "https://x/ext.zip", "ttl_minutes": 15, "codes": [{"name": "М", "code": "123456"}]},
    )
    assert "https://x/ext.zip" in extension and "М: 123456" in extension
