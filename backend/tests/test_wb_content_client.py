import json

import httpx
import respx

from backend.modules.wb_core.infrastructure.wb import CatalogCard, WBContentClient


def card(nm_id: int, **overrides) -> dict:
    return {
        "nmID": nm_id,
        "imtID": 555,
        "vendorCode": f"SKU-{nm_id}",
        "title": "Автомобильный насос",
        "brand": "Karbi",
        "subjectID": 42,
        "subjectName": "Компрессоры автомобильные",
        "photos": [{"big": "https://basket.wb.ru/big.jpg", "c246x328": "https://basket.wb.ru/small.jpg"}],
        "sizes": [{"chrtID": 7, "techSize": "0", "skus": ["2000000000001"]}],
        **overrides,
    }


async def test_content_client_maps_every_identity_field() -> None:
    with respx.mock as router:
        router.post(WBContentClient.endpoint).respond(200, json={"cards": [card(1224320273)], "cursor": {"total": 1}})

        articles = await WBContentClient().get_articles("secret")

    assert articles == [
        CatalogCard(
            article="1224320273",
            vendor_code="SKU-1224320273",
            name="Автомобильный насос",
            imt_id=555,
            brand="Karbi",
            subject_id=42,
            subject_name="Компрессоры автомобильные",
            photo_url="https://basket.wb.ru/small.jpg",
            sizes=[{"chrt_id": 7, "tech_size": "0", "skus": ["2000000000001"]}],
        )
    ]


async def test_content_client_reads_the_trash_alongside_the_catalog() -> None:
    with respx.mock as router:
        router.post(WBContentClient.endpoint).respond(200, json={"cards": [card(1)], "cursor": {"total": 1}})
        router.post(WBContentClient.trash_endpoint).respond(200, json={"cards": [card(2)], "cursor": {"total": 1}})

        catalog = await WBContentClient().get_catalog("secret")

    assert [item.article for item in catalog.active] == ["1"]
    assert [item.article for item in catalog.archived] == ["2"]
    assert catalog.archived_available


async def test_content_client_keeps_the_catalog_when_the_trash_is_unavailable() -> None:
    with respx.mock as router:
        router.post(WBContentClient.endpoint).respond(200, json={"cards": [card(1)], "cursor": {"total": 1}})
        router.post(WBContentClient.trash_endpoint).respond(403)

        catalog = await WBContentClient().get_catalog("secret")

    assert [item.article for item in catalog.active] == ["1"]
    assert catalog.archived == []
    # Without the trash we cannot tell "archived" from "gone", and the caller
    # must know that before it demotes anything.
    assert not catalog.archived_available


async def test_content_client_echoes_back_whatever_cursor_wb_sent() -> None:
    """The trash paginates on trashedAt, the catalog on updatedAt — both must work."""
    with respx.mock as router:
        router.post(WBContentClient.endpoint).respond(200, json={"cards": [], "cursor": {"total": 0}})
        route = router.post(WBContentClient.trash_endpoint).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "cards": [card(index) for index in range(100)],
                        "cursor": {"total": 100, "trashedAt": "2026-08-18T00:00:00Z", "nmID": 99},
                    },
                ),
                httpx.Response(200, json={"cards": [card(100)], "cursor": {"total": 1}}),
            ]
        )

        catalog = await WBContentClient().get_catalog("secret")

    assert len(catalog.archived) == 101
    assert route.call_count == 2
    assert json.loads(route.calls[1].request.read())["settings"]["cursor"] == {
        "limit": 100,
        "trashedAt": "2026-08-18T00:00:00Z",
        "nmID": 99,
    }
