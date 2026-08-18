import respx

from backend.modules.wb_core.infrastructure.wb import WBContentClient


async def test_content_client_maps_nm_id_to_wb_article() -> None:
    with respx.mock as router:
        router.post(WBContentClient.endpoint).respond(
            200,
            json={
                "cards": [
                    {
                        "nmID": 1224320273,
                        "vendorCode": "Компрессоры автомобильные4",
                        "title": "Автомобильный насос",
                    }
                ],
                "cursor": {"total": 1},
            },
        )

        articles = await WBContentClient().get_articles("secret")

    assert articles == [
        {
            "article": "1224320273",
            "vendor_code": "Компрессоры автомобильные4",
            "name": "Автомобильный насос",
        }
    ]
