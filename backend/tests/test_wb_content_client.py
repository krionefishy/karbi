import respx

from backend.modules.wb_core.infrastructure.wb import CatalogCard, WBContentClient
from backend.tests.egress_stub import EgressStub, make_gateway


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


def client() -> WBContentClient:
    return WBContentClient(make_gateway())


async def test_content_client_maps_every_identity_field() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("POST", WBContentClient.endpoint, body={"cards": [card(1224320273)], "cursor": {"total": 1}})

        articles = await client().get_articles("seller-1")

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
    # Конверт шлюза несёт селлера и раздел WB — ключа в нём нет.
    envelope = stub.calls[0]
    assert envelope["seller_id"] == "seller-1" and envelope["api"] == "content"


async def test_content_client_reads_the_trash_alongside_the_catalog() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("POST", WBContentClient.endpoint, body={"cards": [card(1)], "cursor": {"total": 1}})
        stub.on("POST", WBContentClient.trash_endpoint, body={"cards": [card(2)], "cursor": {"total": 1}})

        catalog = await client().get_catalog("seller-1")

    assert [item.article for item in catalog.active] == ["1"]
    assert [item.article for item in catalog.archived] == ["2"]
    assert catalog.archived_available


async def test_content_client_keeps_the_catalog_when_the_trash_is_unavailable() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("POST", WBContentClient.endpoint, body={"cards": [card(1)], "cursor": {"total": 1}})
        stub.on("POST", WBContentClient.trash_endpoint, status=403)

        catalog = await client().get_catalog("seller-1")

    assert [item.article for item in catalog.active] == ["1"]
    assert catalog.archived == []
    # Without the trash we cannot tell "archived" from "gone", and the caller
    # must know that before it demotes anything.
    assert not catalog.archived_available


async def test_content_client_echoes_back_whatever_cursor_wb_sent() -> None:
    """The trash paginates on trashedAt, the catalog on updatedAt — both must work."""
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("POST", WBContentClient.endpoint, body={"cards": [], "cursor": {"total": 0}})
        stub.on(
            "POST",
            WBContentClient.trash_endpoint,
            side_effect=[
                (
                    200,
                    {
                        "cards": [card(index) for index in range(100)],
                        "cursor": {"total": 100, "trashedAt": "2026-08-18T00:00:00Z", "nmID": 99},
                    },
                ),
                (200, {"cards": [card(100)], "cursor": {"total": 1}}),
            ],
        )

        catalog = await client().get_catalog("seller-1")

    trash_calls = stub.requests_to(WBContentClient.trash_endpoint)
    assert len(catalog.archived) == 101
    assert len(trash_calls) == 2
    assert trash_calls[1]["body"]["settings"]["cursor"] == {
        "limit": 100,
        "trashedAt": "2026-08-18T00:00:00Z",
        "nmID": 99,
    }
