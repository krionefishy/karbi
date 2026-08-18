from backend.app.api.router import automation_catalog


def test_automations_returns_only_available_workflows() -> None:
    result = automation_catalog(seller_count=2)

    assert result == [
        {
            "id": "wb-reviews",
            "title": "Мониторинг отзывов Wildberries",
            "description": "Ежедневные снимки отзывов по всем товарам и селлерам Wildberries.",
            "status": "active",
            "last_run_at": None,
            "seller_count": 2,
        }
    ]
