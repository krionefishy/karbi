"""Админ-панель: очереди, карточка с совпадениями, решения по таблице 8.3, пересчёт, удаление файла,
менеджеры, выгрузка CSV и состояние конвейера."""

import csv
import io
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from backend.modules.wb_screenshot_control.application.events import (
    EVENT_DELETE,
    EVENT_SUBMITTED,
    EVENT_VERDICT_READY,
)
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import (
    StorageObjectModel,
    SubmissionModel,
    VerdictEventModel,
)
from backend.modules.wb_screenshot_control.presentation.http.schemas import (
    AdminQueuePage,
    AdminSubmissionDetail,
    ManagerDayRow,
    ManagersOut,
    PipelineOut,
)
from backend.tests.wb_screenshot_control.fakes import desktop_fields
from backend.tests.wb_screenshot_control.http_stand import ADMIN, Stand, open_stand

CHAT = "0f8fad5b-d9cb-469f-a165-70867728950e"
NOTE_REQUIRED = (422, "note_required")


def reason(code: str, severity: str = "info", **detail: Any) -> dict[str, Any]:
    return {"code": code, "severity": severity, "detail": detail}


async def screens(stand: Stand, count: int, role: str = "manager", start: int = 0) -> list[str]:
    headers = await stand.sign_in(role)
    return [(await stand.upload(headers, seed=start + index)).json()["id"] for index in range(count)]


async def resolve(stand: Stand, admin: dict[str, str], submission: str, **body: Any) -> Any:
    return await stand.client.post(f"{ADMIN}/submissions/{submission}/resolve", json=body, headers=admin)


async def test_queues_sort_by_suspicion_and_page_by_cursor(stand: Stand) -> None:
    admin = await stand.sign_in("admin")
    plain, risky, own_dup, audited, accepted = await screens(stand, 5)
    (foreign,) = await screens(stand, 1, "other", start=20)
    await stand.decide(plain, "needs_review", reasons=[reason("weak_key")])
    await stand.decide(risky, "needs_review", reasons=[reason("same_chat_same_text_other_time", "high")])
    await stand.decide(own_dup, "duplicate", duplicate_of=uuid.UUID(accepted), reasons=[reason("case_key")])
    await stand.decide(
        foreign, "duplicate", duplicate_of=uuid.UUID(accepted), reasons=[reason("cross_user_duplicate", "high")]
    )
    await stand.decide(audited, "accepted", reasons=[reason("audit_sample")], audit=True)
    await stand.decide(accepted, "accepted")

    review = AdminQueuePage.model_validate((await stand.client.get(f"{ADMIN}/queue", headers=admin)).json())
    assert [str(item.id) for item in review.items] == [risky, plain]
    assert [item.suspicion for item in review.items] == ["high", "medium"]
    assert review.counts.model_dump() == {"review": 2, "duplicates": 2, "audit": 1}
    assert review.items[0].user.username == f"{stand.prefix}-manager"

    duplicates = (await stand.client.get(f"{ADMIN}/queue", params={"kind": "duplicates"}, headers=admin)).json()
    assert [item["id"] for item in duplicates["items"]] == [foreign, own_dup]
    audit = (await stand.client.get(f"{ADMIN}/queue", params={"kind": "audit"}, headers=admin)).json()
    assert [(item["id"], item["suspicion"]) for item in audit["items"]] == [(audited, "low")]
    by_reason = await stand.client.get(f"{ADMIN}/queue", params={"kind": "all", "reason": "weak_key"}, headers=admin)
    assert [item["id"] for item in by_reason.json()["items"]] == [plain]
    by_user = await stand.client.get(
        f"{ADMIN}/queue", params={"kind": "all", "user_id": str(stand.users["other"])}, headers=admin
    )
    assert [item["id"] for item in by_user.json()["items"]] == [foreign]

    for kind in ("review", "all"):
        seen: list[str] = []
        cursor = None
        while True:
            params: dict[str, Any] = {"kind": kind, "limit": 1, **({"cursor": cursor} if cursor else {})}
            page = (await stand.client.get(f"{ADMIN}/queue", params=params, headers=admin)).json()
            seen += [item["id"] for item in page["items"]]
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert len(seen) == len(set(seen)) == (2 if kind == "review" else 6)


async def test_detail_shows_both_sides_and_no_buyer_name(stand: Stand) -> None:
    admin = await stand.sign_in("admin")
    (original,) = await screens(stand, 1, "other")
    (copy,) = await screens(stand, 1, start=5)
    await stand.decide(original, "accepted", fields=desktop_fields(CHAT))
    await stand.decide(
        copy,
        "duplicate",
        duplicate_of=uuid.UUID(original),
        reasons=[
            reason("case_key", other_submission_id=original),
            reason("cross_user_duplicate", "high", other_submission_id=original),
        ],
        matches=[{"other_submission_id": original, "kind": "case_key", "score": 0.97, "details": {}}],
        fields=desktop_fields(CHAT),
    )
    await stand.store(original, "thumb")

    response = await stand.client.get(f"{ADMIN}/submissions/{copy}", headers=admin)
    assert response.status_code == 200, response.text
    detail = AdminSubmissionDetail.model_validate(response.json())
    assert detail.suspicion == "high" and detail.duplicate_of == uuid.UUID(original)
    assert [item.code for item in detail.reasons] == ["case_key", "cross_user_duplicate"]
    assert detail.reasons[1].other_submission_id == uuid.UUID(original)
    (match,) = detail.matches
    assert (str(match.other_id), match.other_user.username, match.kind, match.score) == (
        original,
        f"{stand.prefix}-other",
        "case_key",
        0.97,
    )
    assert match.thumb_url is not None and match.other_verdict == "accepted"
    assert detail.fields is not None and detail.fields.chat_id == CHAT and detail.fields.buyer_name_seen is True
    assert detail.fields.confidence.chat_id == 0.9 and detail.agreement is not None
    assert detail.file.submissions_count == 1 and detail.file.mime == "image/png"
    assert detail.extraction is not None and detail.extraction.vlm_model == "fake-vlm"
    assert [(event.actor.kind, event.to_verdict) for event in detail.events] == [("system", "duplicate")]
    assert "Иван" not in response.text and 'buyer_name"' not in response.text


async def test_image_links_for_the_admin(stand: Stand) -> None:
    admin = await stand.sign_in("admin")
    (submission,) = await screens(stand, 1)
    missing = (await stand.client.get(f"{ADMIN}/submissions/{submission}/image-url", headers=admin)).json()
    assert missing["available"] is False
    await stand.store(submission)
    original = await stand.client.get(
        f"{ADMIN}/submissions/{submission}/image-url", params={"kind": "original"}, headers=admin
    )
    assert original.json()["available"] is True and "/orig/" in original.json()["url"]
    assert stand.blobs is not None
    assert (stand.blobs.signed[-1]["attachment"], stand.blobs.signed[-1]["content_type"]) == (True, "image/png")
    await stand.client.get(f"{ADMIN}/submissions/{submission}/image-url", headers=admin)
    assert (stand.blobs.signed[-1]["attachment"], stand.blobs.signed[-1]["content_type"]) == (False, "image/jpeg")


@pytest.mark.parametrize(
    ("verdict", "decision", "note", "expected"),
    [
        ("needs_review", "approve", "", "accepted"),
        ("needs_review", "decline", "", NOTE_REQUIRED),
        ("needs_review", "decline", "не тот кейс", "rejected"),
        ("accepted", "decline", "", NOTE_REQUIRED),
        ("accepted", "decline", "аудит не прошёл", "rejected"),
        ("accepted", "approve", "", (409, "transition_not_allowed")),
        ("rejected", "approve", "", (409, "transition_not_allowed")),
        ("duplicate", "not_duplicate", "", "accepted"),
        ("duplicate", "decline", "x", (409, "transition_not_allowed")),
        ("accepted", "audit_ok", "", (409, "transition_not_allowed")),
    ],
)
async def test_resolve_follows_the_transition_table(
    stand: Stand, verdict: str, decision: str, note: str, expected: str | tuple[int, str]
) -> None:
    admin = await stand.sign_in("admin")
    (submission,) = await screens(stand, 1)
    await stand.decide(submission, verdict)
    response = await resolve(stand, admin, submission, decision=decision, note=note)
    if isinstance(expected, tuple):
        assert (response.status_code, response.headers["X-Error-Code"]) == expected
        assert (await stand.submission(submission)).verdict == verdict
        return
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["verdict"] == expected and detail["auto_verdict"] == verdict
    assert detail["reviewed_by"]["id"] == str(stand.users["admin"])
    assert detail["events"][-1]["actor"] == {
        "kind": "admin",
        "user": {"id": str(stand.users["admin"]), "username": f"{stand.prefix}-admin"},
    }
    ready = [event for event in await stand.outbox(EVENT_VERDICT_READY) if event["actor"] == "admin"]
    assert [(event["previous_verdict"], event["verdict"]) for event in ready] == [(verdict, expected)]


async def test_duplicate_target_audit_and_case_key_conflict(stand: Stand) -> None:
    admin = await stand.sign_in("admin")
    first, second, third, audited = await screens(stand, 4)
    await stand.decide(first, "accepted", fields=desktop_fields(CHAT))
    await stand.decide(second, "needs_review", fields=desktop_fields(CHAT))
    await stand.decide(third, "duplicate", duplicate_of=uuid.UUID(first))
    await stand.decide(audited, "accepted", audit=True)

    conflict = await resolve(stand, admin, second, decision="approve")
    assert (conflict.status_code, conflict.headers["X-Error-Code"]) == (409, "exact_key_conflict")
    nowhere = await resolve(stand, admin, second, decision="duplicate_of", duplicate_of=str(uuid.uuid4()))
    assert (nowhere.status_code, nowhere.headers["X-Error-Code"]) == (422, "duplicate_target_invalid")
    itself = await resolve(stand, admin, second, decision="duplicate_of", duplicate_of=second)
    assert itself.status_code == 422
    marked = await resolve(stand, admin, second, decision="duplicate_of", duplicate_of=third, outcome="amended")
    assert marked.status_code == 200, marked.text
    assert (marked.json()["verdict"], marked.json()["duplicate_of"], marked.json()["outcome"]) == (
        "duplicate",
        first,
        "amended",
    )
    assert marked.json()["outcome_source"] == "admin"

    accepted_to_dup = await resolve(stand, admin, audited, decision="duplicate_of", duplicate_of=first)
    assert accepted_to_dup.headers["X-Error-Code"] == "note_required"
    ok = await resolve(stand, admin, audited, decision="audit_ok", note="сверил с кабинетом")
    assert ok.status_code == 200 and ok.json()["verdict"] == "accepted"
    assert (await stand.submission(audited)).audit_done_at is not None
    audit_queue = (await stand.client.get(f"{ADMIN}/queue", params={"kind": "audit"}, headers=admin)).json()
    assert audit_queue["items"] == []


async def test_reprocess_and_erase_file(stand: Stand) -> None:
    admin = await stand.sign_in("admin")
    (submission,) = await screens(stand, 1)
    await stand.decide(submission, "rejected", reasons=[reason("unreadable")])

    response = await stand.client.post(f"{ADMIN}/submissions/{submission}/reprocess", headers=admin)
    assert (response.status_code, response.json()) == (202, {"id": submission, "stage": "processing"})
    row = await stand.submission(submission)
    assert (row.verdict, row.reasons, row.stage) == (None, [], "processing")
    assert len([event for event in await stand.outbox(EVENT_SUBMITTED) if event["submission_id"] == submission]) == 2
    async with stand.database.session() as session:
        events = list(await session.scalars(select(VerdictEventModel).where(VerdictEventModel.submission_id == row.id)))
    assert [(event.actor.split(":")[0], event.from_verdict, event.to_verdict) for event in events] == [
        ("system", None, "rejected"),
        ("admin", "rejected", None),
    ]

    await stand.store(submission)
    erased = await stand.client.delete(f"{ADMIN}/submissions/{submission}/file", headers=admin)
    assert erased.status_code == 202, erased.text
    assert {item["state"] for item in erased.json()["objects"]} == {"delete_requested"}
    deletes = await stand.outbox(EVENT_DELETE)
    assert {(event["kind"], event["reason"]) for event in deletes} == {("orig", "admin"), ("thumb", "admin")}
    again = await stand.client.delete(f"{ADMIN}/submissions/{submission}/file", headers=admin)
    assert again.status_code == 202 and len(await stand.outbox(EVENT_DELETE)) == 2


async def test_erase_without_s3_clears_staging(stand: Stand) -> None:
    admin = await stand.sign_in("admin")
    (submission,) = await screens(stand, 1)
    staged = [path for path in stand.staging.rglob("*") if path.is_file()]
    assert len(staged) == 1
    erased = await stand.client.delete(f"{ADMIN}/submissions/{submission}/file", headers=admin)
    assert [item["state"] for item in erased.json()["objects"]] == ["deleted"]
    assert not staged[0].exists()
    async with stand.database.session() as session:
        stored = await session.scalar(select(StorageObjectModel))
        assert stored is not None and stored.staging_path is None


async def test_managers_stats_flags_and_export(tmp_path: Path) -> None:
    async with open_stand(tmp_path, roles=("admin", "manager", "other")) as stand:
        admin = await stand.sign_in("admin")
        (seed_id,) = await screens(stand, 1)
        await stand.decide(seed_id, "accepted")
        template = await stand.submission(seed_id)
        now = datetime.now(UTC)
        async with stand.database.session() as session:
            for index in range(24):
                verdict = "duplicate" if index < 4 else "accepted"
                session.add(
                    SubmissionModel(
                        user_id=stand.users["manager"],
                        channel="web",
                        client_submission_id=f"bulk-{index}",
                        file_id=template.file_id,
                        received_at=now,
                        outcome="positive",
                        stage="decided",
                        verdict=verdict,
                        reasons=[reason("upload_burst", "warn")] if index == 5 else [],
                    )
                )
            await session.commit()
        (other_id,) = await screens(stand, 1, "other", start=30)
        await stand.decide(other_id, "needs_review")

        response = await stand.client.get(f"{ADMIN}/managers", headers=admin)
        stats = ManagersOut.model_validate(response.json())
        first, second = stats.items
        assert (first.user.username, first.total, first.accepted, first.positive, first.duplicate) == (
            f"{stand.prefix}-manager",
            25,
            21,
            21,
            4,
        )
        assert first.duplicate_rate == 0.16 and set(first.flags) == {"high_duplicate_rate", "upload_burst"}
        assert (second.total, second.needs_review, second.review_rate, second.flags) == (1, 1, 1.0, [])

        export = await stand.client.get(f"{ADMIN}/managers/export", headers=admin)
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("text/csv")
        assert export.headers["content-disposition"].startswith("attachment;")
        assert export.content.startswith("﻿".encode())
        rows = list(csv.reader(io.StringIO(export.content.decode("utf-8-sig")), delimiter=";"))
        assert rows[0] == list(ManagerDayRow.model_fields)
        assert sorted((row[2], row[3], row[4], row[-1]) for row in rows[1:]) == sorted(
            [(f"{stand.prefix}-manager", "25", "21", "true"), (f"{stand.prefix}-other", "1", "0", "true")]
        )
        manager = await stand.sign_in("manager")
        assert (await stand.client.get(f"{ADMIN}/managers/export", headers=manager)).status_code == 403


async def test_pipeline_numbers(stand: Stand) -> None:
    admin = await stand.sign_in("admin")
    first, second = await screens(stand, 2)
    await stand.decide(first, "accepted", fields=desktop_fields())
    await stand.store(first)

    pipeline = PipelineOut.model_validate((await stand.client.get(f"{ADMIN}/pipeline", headers=admin)).json())
    assert pipeline.stages.processing == 1 and pipeline.oldest_processing_age_s is not None
    assert pipeline.p50_s is not None and pipeline.vlm_error_rate_1h == 0.0
    assert pipeline.tokens_today == 1200 and pipeline.channels_today.web == 2
    assert pipeline.storage.objects["stored"] == 2 and pipeline.storage.objects["staged"] == 1
    assert pipeline.storage.stored_bytes > 0 and pipeline.storage.staging_used_percent is not None
    assert second
