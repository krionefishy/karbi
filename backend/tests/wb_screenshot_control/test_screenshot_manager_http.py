"""Ручки менеджера на живой базе: свои скрины, причины, исход, сводка по МСК, миниатюры."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.modules.wb_screenshot_control.application.events import EVENT_EXTRACTED, EVENT_SUBMITTED
from backend.modules.wb_screenshot_control.infrastructure.wiring import ScreenshotControlServices
from backend.modules.wb_screenshot_control.presentation.http.schemas import SubmissionOut, SubmissionPage, Summary
from backend.tests.wb_screenshot_control.fakes import FakeDecider, FakeExtractor, desktop_fields, ok_result
from backend.tests.wb_screenshot_control.http_stand import MANAGER, Stand, open_stand

CHAT = "0f8fad5b-d9cb-469f-a165-70867728950e"


async def test_lists_only_own_screenshots_with_filters_and_cursor(stand: Stand) -> None:
    manager = await stand.sign_in("manager")
    other = await stand.sign_in("other")
    mine = [(await stand.upload(manager, seed=seed)).json()["id"] for seed in range(5)]
    foreign = (await stand.upload(other, seed=10)).json()["id"]
    await stand.decide(mine[0], "accepted")
    await stand.decide(mine[1], "needs_review", reasons=[{"code": "weak_key", "severity": "info", "detail": {}}])

    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = SubmissionPage.model_validate(
            (await stand.client.get(f"{MANAGER}/submissions", params=params, headers=manager)).json()
        )
        seen += [str(item.id) for item in page.items]
        cursor = page.next_cursor
        if cursor is None:
            break
    assert seen == list(reversed(mine))

    review = await stand.client.get(f"{MANAGER}/submissions", params={"status": "needs_review"}, headers=manager)
    assert [item["id"] for item in review.json()["items"]] == [mine[1]]
    assert review.json()["items"][0]["reason_code"] == "under_review"
    processing = await stand.client.get(f"{MANAGER}/submissions", params={"status": "processing"}, headers=manager)
    assert len(processing.json()["items"]) == 3
    by_outcome = await stand.client.get(f"{MANAGER}/submissions", params={"outcome": "amended"}, headers=manager)
    assert by_outcome.json()["items"] == []

    stranger = await stand.client.get(f"{MANAGER}/submissions/{foreign}", headers=manager)
    assert (stranger.status_code, stranger.headers["X-Error-Code"]) == (404, "not_found")
    picture = await stand.client.get(f"{MANAGER}/submissions/{foreign}/image-url", headers=manager)
    assert picture.status_code == 404
    change = await stand.client.patch(
        f"{MANAGER}/submissions/{foreign}/outcome", json={"outcome": "deleted"}, headers=manager
    )
    assert change.status_code == 404


async def test_card_shows_what_was_read_without_revealing_the_original(stand: Stand) -> None:
    manager = await stand.sign_in("manager")
    other = await stand.sign_in("other")
    original = (await stand.upload(other, seed=1)).json()["id"]
    copy = (await stand.upload(manager, seed=2)).json()["id"]
    await stand.decide(original, "accepted", fields=desktop_fields(CHAT))
    await stand.decide(
        copy,
        "duplicate",
        duplicate_of=uuid.UUID(original),
        reasons=[
            {"code": "case_key", "severity": "info", "detail": {"other_submission_id": original}},
            {"code": "cross_user_duplicate", "severity": "high", "detail": {"other_submission_id": original}},
        ],
        fields=desktop_fields(CHAT),
    )
    await stand.store(copy, "thumb")

    response = await stand.client.get(f"{MANAGER}/submissions/{copy}", headers=manager)
    card = SubmissionOut.model_validate(response.json())
    assert (card.status, card.reason_code, card.reason_text) == (
        "duplicate",
        "already_uploaded",
        "Этот скриншот уже загружен ранее",
    )
    assert card.duplicate_received_at == (await stand.submission(original)).received_at
    assert card.fields is not None and card.fields.chat_tail == CHAT[-6:] and card.fields.stars == 1
    assert card.fields.text_preview and card.fields.text_preview.startswith("товар пришел")
    assert card.thumb_url and "/thumb/" in card.thumb_url
    body = response.text
    for secret in (original, str(stand.users["other"]), "cross_user", "case_key", CHAT[:-6], "Иван"):
        assert secret not in body


async def test_image_url_is_presigned_only_for_an_uploaded_thumbnail(stand: Stand) -> None:
    manager = await stand.sign_in("manager")
    submission = (await stand.upload(manager)).json()["id"]

    staged = await stand.client.get(f"{MANAGER}/submissions/{submission}/image-url", headers=manager)
    assert staged.json() == {"url": None, "expires_at": None, "available": False}

    await stand.store(submission)
    ready = (await stand.client.get(f"{MANAGER}/submissions/{submission}/image-url", headers=manager)).json()
    assert ready["available"] is True and "/thumb/" in ready["url"]
    expires = datetime.fromisoformat(ready["expires_at"])
    assert timedelta(seconds=290) < expires - datetime.now(UTC) <= timedelta(seconds=300)
    assert stand.blobs is not None
    assert stand.blobs.signed[-1] | {"key": ""} == {
        "key": "",
        "ttl": 300,
        "content_type": "image/jpeg",
        "attachment": False,
    }
    original = await stand.client.get(
        f"{MANAGER}/submissions/{submission}/image-url", params={"kind": "original"}, headers=manager
    )
    assert original.status_code == 422


async def test_without_s3_there_are_no_links_and_nothing_fails(tmp_path: Path) -> None:
    async with open_stand(tmp_path, s3=False, roles=("manager",)) as stand:
        manager = await stand.sign_in("manager")
        submission = (await stand.upload(manager)).json()["id"]
        await stand.store(submission)
        answer = await stand.client.get(f"{MANAGER}/submissions/{submission}/image-url", headers=manager)
        assert answer.json()["available"] is False
        card = await stand.client.get(f"{MANAGER}/submissions/{submission}", headers=manager)
        assert card.json()["thumb_url"] is None


async def test_outcome_changes_until_the_verdict(stand: Stand) -> None:
    manager = await stand.sign_in("manager")
    waiting = (await stand.upload(manager, outcome=None)).json()
    assert waiting["status"] == "awaiting_outcome"

    changed = await stand.client.patch(
        f"{MANAGER}/submissions/{waiting['id']}/outcome", json={"outcome": "deleted"}, headers=manager
    )
    assert (changed.status_code, changed.json()["outcome"], changed.json()["status"]) == (200, "deleted", "processing")
    assert (await stand.submission(waiting["id"])).outcome_source == "form"

    await stand.decide(waiting["id"], "accepted")
    locked = await stand.client.patch(
        f"{MANAGER}/submissions/{waiting['id']}/outcome", json={"outcome": "positive"}, headers=manager
    )
    assert (locked.status_code, locked.headers["X-Error-Code"]) == (409, "outcome_locked")
    bad = await stand.client.patch(
        f"{MANAGER}/submissions/{waiting['id']}/outcome", json={"outcome": "good"}, headers=manager
    )
    assert bad.status_code == 422


async def test_choosing_an_outcome_starts_the_decision(stand: Stand) -> None:
    """Распознанный скрин без исхода ждёт; исход запускает решение, а отказ `no_outcome` окончателен."""
    manager = await stand.sign_in("manager")
    submission = (await stand.upload(manager, outcome=None)).json()["id"]
    await stand.recognised(submission, desktop_fields())
    card = (await stand.client.get(f"{MANAGER}/submissions/{submission}", headers=manager)).json()
    assert (card["status"], card["reason_code"]) == ("awaiting_outcome", None)

    chosen = await stand.client.patch(
        f"{MANAGER}/submissions/{submission}/outcome", json={"outcome": "amended"}, headers=manager
    )
    assert (chosen.status_code, chosen.json()["status"]) == (200, "processing")
    row = await stand.submission(submission)
    assert (row.stage, row.verdict, row.outcome) == ("extracted", None, "amended")
    assert [event["submission_id"] for event in await stand.outbox(EVENT_EXTRACTED)] == [submission]

    expired = (await stand.upload(manager, seed=3, outcome=None)).json()["id"]
    await stand.decide(expired, "rejected", reasons=[{"code": "no_outcome", "severity": "info", "detail": {}}])
    late = await stand.client.patch(
        f"{MANAGER}/submissions/{expired}/outcome", json={"outcome": "amended"}, headers=manager
    )
    assert (late.status_code, late.headers["X-Error-Code"]) == (409, "outcome_locked")


async def test_summary_counts_by_moscow_days(tmp_path: Path) -> None:
    async with open_stand(tmp_path, roles=("manager", "other")) as stand:
        manager = await stand.sign_in("manager")
        other = await stand.sign_in("other")
        ids = [
            (await stand.upload(manager, seed=seed, outcome=outcome)).json()["id"]
            for seed, outcome in enumerate(["positive", "positive", "amended", "deleted", "positive", "positive", None])
        ]
        await stand.upload(other, seed=50)
        for item, verdict in zip(
            ids[:6], ["accepted", "accepted", "accepted", "duplicate", "needs_review", "rejected"], strict=True
        ):
            await stand.decide(item, verdict)
        # 22.10 21:30 UTC — уже 23.10 по Москве; 22.10 20:59 UTC — ещё 22.10.
        for item in ids:
            await stand.set_received(item, datetime(2026, 10, 22, 21, 30, tzinfo=UTC))
        await stand.set_received(ids[1], datetime(2026, 10, 22, 20, 59, tzinfo=UTC))

        day = await stand.client.get(f"{MANAGER}/summary", params={"from": "2026-10-23"}, headers=manager)
        summary = Summary.model_validate(day.json())
        assert (summary.period.from_.isoformat(), summary.period.to.isoformat()) == ("2026-10-23", "2026-10-23")
        assert summary.counts.model_dump() == {"positive": 1, "amended": 1, "deleted": 0, "accepted_total": 2}
        assert (summary.duplicates, summary.pending_review, summary.rejected) == (1, 1, 1)
        assert (summary.awaiting_outcome, summary.processing) == (1, 0)
        assert summary.mode == "shadow" and summary.preliminary is True

        before = await stand.client.get(
            f"{MANAGER}/summary", params={"from": "2026-10-22", "to": "2026-10-22"}, headers=manager
        )
        assert before.json()["counts"]["accepted_total"] == 1
        too_long = await stand.client.get(
            f"{MANAGER}/summary", params={"from": "2025-01-01", "to": "2026-10-23"}, headers=manager
        )
        assert (too_long.status_code, too_long.headers["X-Error-Code"]) == (422, "period_too_long")

    async with open_stand(tmp_path, mode="enforce", kpi_from="2026-10-01", roles=("manager",)) as stand:
        manager = await stand.sign_in("manager")
        live = await stand.client.get(f"{MANAGER}/summary", params={"from": "2026-10-02"}, headers=manager)
        early = await stand.client.get(f"{MANAGER}/summary", params={"from": "2026-09-30"}, headers=manager)
        assert (live.json()["preliminary"], early.json()["preliminary"]) == (False, True)


async def test_screenshot_goes_through_the_pipeline_to_a_verdict(stand: Stand) -> None:
    """Сквозной путь: приём по HTTP, консьюмеры распознавания и решения с фейками, ответ менеджеру."""
    manager = await stand.sign_in("manager")
    submission = (await stand.upload(manager, seed=7, outcome="positive")).json()["id"]
    services = ScreenshotControlServices(stand.database, stand.application.settings, blobs=stand.blobs)
    services.blobs = None  # приём шёл без бакета: миниатюра остаётся в staging

    submitted = [event for event in await stand.outbox(EVENT_SUBMITTED) if event["submission_id"] == submission]
    await services.extraction(FakeExtractor([ok_result(desktop_fields(CHAT))])).handle(submitted[0])
    extracted = [event for event in await stand.outbox(EVENT_EXTRACTED) if event["submission_id"] == submission]
    await services.decision(FakeDecider("accepted")).handle(extracted[0])

    card = (await stand.client.get(f"{MANAGER}/submissions/{submission}", headers=manager)).json()
    assert (card["status"], card["reason_code"], card["reason_text"]) == ("accepted", None, "Принят")
    assert card["fields"]["chat_tail"] == CHAT[-6:] and card["thumb_url"] is None
    summary = (await stand.client.get(f"{MANAGER}/summary", headers=manager)).json()
    assert summary["counts"]["positive"] == 1
