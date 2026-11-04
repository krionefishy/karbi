import { X } from "lucide-react";
import { type FormEvent, useMemo, useState } from "react";

import type { ProductReviewHistory } from "../features/reviews/types";

interface Props {
  product: ProductReviewHistory;
  targets: ProductReviewHistory[];
  onClose: () => void;
  onSubmit: (mainArticle: string) => void;
}

function normalize(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLowerCase();
}

/** Ближе к болванке — товары того же предмета, затем с большим числом фото. */
function rank(product: ProductReviewHistory, targets: ProductReviewHistory[]): ProductReviewHistory[] {
  return [...targets].sort((left, right) => {
    const sameLeft = left.subject_name === product.subject_name ? 0 : 1;
    const sameRight = right.subject_name === product.subject_name ? 0 : 1;
    return sameLeft - sameRight || (right.photo_count ?? 0) - (left.photo_count ?? 0) || left.name.localeCompare(right.name, "ru");
  });
}

export function AttachToMainDialog({ product, targets, onClose, onSubmit }: Props) {
  const ranked = useMemo(() => rank(product, targets), [product, targets]);
  const [query, setQuery] = useState("");
  const [chosen, setChosen] = useState(ranked[0]?.article ?? "");
  const needle = normalize(query);
  const visible = needle
    ? ranked.filter((target) =>
        [target.name, target.article, target.vendor_code].some((field) => normalize(field).includes(needle)),
      )
    : ranked;

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (chosen) onSubmit(chosen);
  }

  return (
    <div className="modal-overlay" role="presentation">
      <section className="seller-dialog attach-dialog" role="dialog" aria-modal="true" aria-labelledby="attach-dialog-title">
        <button className="dialog-close" onClick={onClose} aria-label="Закрыть">
          <X size={18} />
        </button>
        <h2 id="attach-dialog-title">Привязать к основному товару</h2>
        <p className="muted">
          «{product.name}», WB {product.article}. Отзывы этой карточки будут складываться в строку основного
          товара. На Wildberries ничего не меняется.
        </p>
        <form className="seller-form" onSubmit={submit}>
          <label>
            <span className="field-label">Поиск основного товара</span>
            <input
              name="attach_query"
              type="search"
              placeholder="Название, артикул WB или продавца"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <div className="attach-choices" role="radiogroup" aria-label="Основной товар">
            {visible.length === 0 && <p className="muted">Ничего не нашлось.</p>}
            {visible.map((target) => (
              <label className={`attach-choice${chosen === target.article ? " attach-choice-selected" : ""}`} key={target.id}>
                <input
                  type="radio"
                  name="main_article"
                  value={target.article}
                  checked={chosen === target.article}
                  onChange={() => setChosen(target.article)}
                />
                <span>
                  <strong>{target.name}</strong>
                  <small>
                    WB {target.article}
                    {target.vendor_code && ` · ${target.vendor_code}`}
                    {target.photo_count !== null && ` · ${target.photo_count} фото`}
                  </small>
                </span>
              </label>
            ))}
          </div>
          <div className="dialog-actions">
            <button type="button" className="secondary-button" onClick={onClose}>
              Отмена
            </button>
            <button type="submit" className="primary-button" disabled={!chosen}>
              Привязать
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
