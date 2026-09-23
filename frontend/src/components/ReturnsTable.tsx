import type { Claim, ReturnItem } from "../features/returns/types";

const dateFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium" });
const momentFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });
const moneyFormatter = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });

const RETURNS_TEMPLATE = ["260px", "230px", "140px", "220px", "130px", "150px", "150px"].join(" ");
const RETURNS_STORAGE_TEMPLATE = [...RETURNS_TEMPLATE.split(" "), "130px", "130px"].join(" ");
const CLAIMS_TEMPLATE = ["260px", "110px", "380px", "160px", "140px", "150px", "150px"].join(" ");

function day(value: string | null): string {
  return value ? dateFormatter.format(new Date(value)) : "—";
}

function stamp(value: string | null): string {
  return value ? momentFormatter.format(new Date(value)) : "—";
}

/** Класс для срока: подсвечиваем то, что истекает сегодня или уже прошло. */
function deadlineClass(value: string | null): string | undefined {
  if (!value) return undefined;
  const left = (new Date(value).getTime() - Date.now()) / 86_400_000;
  if (left < 0) return "returns-deadline-over";
  if (left < 1) return "returns-deadline-soon";
  return undefined;
}

function Product({ title, nmId, sticker }: { title: string; nmId: number; sticker: string }) {
  return (
    <span className="stocks-product">
      <strong>{title || "—"}</strong>
      <em>
        {nmId ? (
          <a className="wb-article-link" href={`https://www.wildberries.ru/catalog/${nmId}/detail.aspx`} target="_blank" rel="noreferrer">
            {nmId}
          </a>
        ) : null}
        {sticker ? ` · стикер ${sticker}` : ""}
      </em>
    </span>
  );
}

/** Возвраты: товар, ПВЗ, статус, причина; у готовых к выдаче — сроки хранения. */
export function ReturnsTable({ rows, empty, storage = false }: { rows: ReturnItem[]; empty: string; storage?: boolean }) {
  if (rows.length === 0) {
    return <p className="muted">{empty}</p>;
  }
  return (
    <section
      className="checklist-scroll stocks-scroll"
      style={{ "--stocks-template": storage ? RETURNS_STORAGE_TEMPLATE : RETURNS_TEMPLATE } as React.CSSProperties}
    >
      <div className="stocks-head">
        <span className="checklist-sticky">Товар</span>
        <span>ПВЗ</span>
        <span>Статус</span>
        <span>Тип и причина</span>
        <span>Заказ от</span>
        <span>Статус с</span>
        <span>Срок хранения</span>
        {storage && <span>Бесплатно до</span>}
        {storage && <span>Забрать до</span>}
      </div>
      {rows.map((row) => (
        <div className="stocks-row" key={row.shk_id}>
          <span className="checklist-sticky">
            <Product title={row.title} nmId={row.nm_id} sticker={row.sticker_id} />
          </span>
          <span>{row.dst_office_address || "—"}</span>
          <span>{row.status_title}</span>
          <span className="stocks-product">
            <strong>{row.return_type || "—"}</strong>
            {row.reason && <em>{row.reason}</em>}
          </span>
          <span>{day(row.order_dt)}</span>
          <span>{stamp(row.status_changed_at)}</span>
          <span>{row.pickup_deadline ? "7 дн., 3 бесплатно" : "—"}</span>
          {storage && <span className={deadlineClass(row.free_until)}>{day(row.free_until)}</span>}
          {storage && <span className={deadlineClass(row.pickup_deadline)}>{day(row.pickup_deadline)}</span>}
        </div>
      ))}
    </section>
  );
}

/** Заявки покупателей: товар, цена, комментарий и фото, срок ответа. */
export function ReturnsClaims({ claims, empty }: { claims: Claim[]; empty: string }) {
  if (claims.length === 0) {
    return empty ? <p className="muted">{empty}</p> : null;
  }
  return (
    <section className="checklist-scroll stocks-scroll" style={{ "--stocks-template": CLAIMS_TEMPLATE } as React.CSSProperties}>
      <div className="stocks-head">
        <span className="checklist-sticky">Товар</span>
        <span>Цена</span>
        <span>Комментарий покупателя</span>
        <span>Фото</span>
        <span>Создана</span>
        <span>Ответить до</span>
        <span>Решение</span>
      </div>
      {claims.map((claim) => (
        <div className="stocks-row" key={claim.id}>
          <span className="checklist-sticky">
            <Product title={claim.name} nmId={claim.nm_id} sticker="" />
          </span>
          <span className="stocks-cell">{moneyFormatter.format(claim.price)} ₽</span>
          <span className="returns-claim-comment">{claim.user_comment || "—"}</span>
          <span className="returns-photos">
            {claim.photos.length === 0
              ? "—"
              : claim.photos.map((url, index) => (
                  <a key={url} href={url} target="_blank" rel="noreferrer">
                    {index + 1}
                  </a>
                ))}
          </span>
          <span>{stamp(claim.created_at)}</span>
          <span className={claim.status === 0 && !claim.is_archive ? deadlineClass(claim.review_deadline) : undefined}>
            {claim.status === 0 && !claim.is_archive ? day(claim.review_deadline) : "—"}
          </span>
          <span className="returns-claim-comment">
            {claim.status === 0 && !claim.is_archive ? "На рассмотрении" : claim.wb_comment || "Решено"}
          </span>
        </div>
      ))}
    </section>
  );
}
