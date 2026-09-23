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
