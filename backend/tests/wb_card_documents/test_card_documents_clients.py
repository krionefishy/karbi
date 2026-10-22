import pytest
import respx

from backend.modules.wb_card_documents.infrastructure.wb import WBCardDocumentsClient
from backend.modules.wb_core.infrastructure.wb import WBPermanentError
from backend.tests.egress_stub import EgressStub, make_gateway

CARDS = "/content/v2/get/cards/list"
SELLER = "seller-1"


async def test_cards_are_read_through_the_gateway_with_their_documents() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on(
            "POST",
            CARDS,
            body={
                "cards": [
                    {
                        "nmID": 1504904633,
                        "vendorCode": "VK-1",
                        "documents": {
                            "items": [
                                {
                                    "number": "ЕАЭС N RU Д-CN.РА09.В.46306/26",
                                    "endDate": "2031-10-08T00:00:00Z",
                                    "verdict": {"verified": True, "status": 1, "reason": "valid"},
                                }
                            ]
                        },
                    },
                    {"nmID": 1175671003},
                ],
                "cursor": {"updatedAt": "2026-10-22T07:31:40Z", "nmID": 1175671003, "total": 2},
            },
        )

        first, second = await WBCardDocumentsClient(make_gateway()).cards(SELLER)

        assert stub.calls[0]["api"] == "content"
        assert stub.calls[0]["seller_id"] == SELLER

    assert first.nm_id == 1504904633 and first.documents[0].verdict is not None
    assert (second.nm_id, second.has_block) == (1175671003, False)


async def test_a_key_without_content_access_is_a_permanent_error() -> None:
    with respx.mock as router:
        EgressStub(router).on("POST", CARDS, status=401, body={"title": "unauthorized"})

        with pytest.raises(WBPermanentError):
            await WBCardDocumentsClient(make_gateway()).cards(SELLER)
