import type { Dialog, DialogGroup, DialogOutcome } from "../features/reviewChats/types";

const momentFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });

const TEMPLATE = ["125px", "250px", "170px", "170px", "125px", "minmax(320px, 1fr)"].join(" ");

const GROUP_LABEL: Record<DialogGroup, string> = {
  followed: "с нашим сообщением",
  early: "ответил раньше нашего",
  missed: "пропуск рассылки",
  before: "до запуска",
};

const OUTCOME_LABEL: Record<DialogOutcome, string> = {
  replied: "Ответил",
  silent: "Не ответил",
  pending: "Ждём ответа",
};

function stamp(value: string | null): string {
  return value ? momentFormatter.format(new Date(value)) : "—";
}

export function ReviewChatsDialogs({ dialogs, empty }: { dialogs: Dialog[]; empty: string }) {
  return (
    <section className="checklist-scroll stocks-scroll" style={{ "--stocks-template": TEMPLATE } as React.CSSProperties}>
      <div className="stocks-head">
        <span className="checklist-sticky">Автосообщение WB</span>
        <span>Товар</span>
        <span>Наше сообщение</span>
        <span>Исход</span>
        <span>Ответ покупателя</span>
        <span>Текст ответа</span>
      </div>
      {dialogs.map((dialog) => (
        <div className="stocks-row" key={`${dialog.chat_id}:${dialog.prompt_at}`}>
          <span className="checklist-sticky">{stamp(dialog.prompt_at)}</span>
          <span className="stocks-product">
            <strong>{dialog.product_name || "—"}</strong>
            <em>
              {dialog.nm_id ? (
                <a
                  className="wb-article-link"
                  href={`https://www.wildberries.ru/catalog/${dialog.nm_id}/detail.aspx`}
                  target="_blank"
                  rel="noreferrer"
                >
                  {dialog.nm_id}
                </a>
              ) : (
                // В уже существующем чате WB карточку товара к автосообщению не прикладывает.
                "артикул не передан"
              )}
            </em>
          </span>
          <span className="penalty-kind" title={dialog.follow_up_text ?? undefined}>
            {dialog.follow_up_at ? (
              <>
                <strong>{stamp(dialog.follow_up_at)}</strong>
                {dialog.follow_up_late && <em className="stocks-unknown">после ответа покупателя</em>}
              </>
            ) : (
              <em className="stocks-unknown">не уходило</em>
            )}
          </span>
          <span className="penalty-kind">
            <strong className={`chats-outcome chats-outcome-${dialog.outcome}`}>{OUTCOME_LABEL[dialog.outcome]}</strong>
            <em>{GROUP_LABEL[dialog.group]}</em>
          </span>
          <span>{stamp(dialog.reply_at)}</span>
          <span className="chats-reply">
            {dialog.reply_text || (dialog.reply_has_attachments ? "вложение без текста" : "")}
          </span>
        </div>
      ))}
      {dialogs.length === 0 && <span className="fbs-row-empty">{empty}</span>}
    </section>
  );
}
