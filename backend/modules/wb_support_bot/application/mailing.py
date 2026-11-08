from datetime import datetime

from backend.modules.wb_support_bot.domain import Mailing, decide, manual_valid
from backend.modules.wb_support_bot.infrastructure.postgres import VERDICT_DONE, DialogModel, VerdictModel


def mailing_for(
    dialog: DialogModel,
    verdict_row: VerdictModel | None,
    *,
    mode: str,
    product_enabled: bool,
    now: datetime,
) -> Mailing:
    """Итоговое решение по диалогу: общее для отправки и для страницы, чтобы они не расходились."""
    manual = (
        dialog.manual_verdict
        if manual_valid(
            dialog.manual_verdict,
            manual_fingerprint=dialog.manual_fingerprint,
            manual_until=dialog.manual_until,
            tail_fingerprint=dialog.tail_fingerprint,
            now=now,
        )
        else None
    )
    verdict = verdict_row.verdict if verdict_row is not None and verdict_row.status == VERDICT_DONE else None
    return decide(
        mode=mode,
        state=dialog.state,
        no_contact=dialog.no_contact,
        excluded=dialog.excluded,
        reply_hold=dialog.reply_hold,
        product_enabled=product_enabled,
        verdict=verdict,
        manual=manual,
        manual_until=dialog.manual_until,
    )
