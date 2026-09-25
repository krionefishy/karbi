import { useMutation } from "@tanstack/react-query";
import { Download, ListChecks } from "lucide-react";
import { useState } from "react";

import { ApiError } from "../api/http";
import { Shell } from "../components/Shell";
import { buildStickers, checkStickers } from "../features/boxStickers/api";
import type { BoxStickerBox, BoxStickerPlan } from "../features/boxStickers/types";

const AUTOMATION_TITLE = "Стикеры коробов";

function shortShk(shk: string) {
  return shk.length === 7 ? `${shk.slice(0, 3)} ${shk.slice(3)}` : shk;
}

function contents(box: BoxStickerBox) {
  return box.items
    .map((item) => {
      const name = item.vendor_code || item.barcode;
      const size = item.tech_size && item.tech_size !== "0" ? ` (${item.tech_size})` : "";
      return `${name}${size} × ${item.quantity}`;
    })
    .join(", ");
}

export function BoxStickersPage() {
  const [workbook, setWorkbook] = useState<File | null>(null);
  const [stickers, setStickers] = useState<File | null>(null);
  const [stamp, setStamp] = useState(true);
  const [plan, setPlan] = useState<BoxStickerPlan | null>(null);
  const [error, setError] = useState("");
  const fail = (fallback: string) => (reason: unknown) =>
    setError(reason instanceof ApiError ? reason.message : fallback);

  const checkMutation = useMutation({
    mutationFn: () => checkStickers(workbook as File, stickers as File),
    onSuccess: (result) => {
      setError("");
      setPlan(result);
    },
    onError: fail("Не удалось сверить файлы"),
  });
  const buildMutation = useMutation({
    mutationFn: () => buildStickers(workbook as File, stickers as File, stamp),
    onSuccess: ({ blob, filename }) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename ?? "box_stickers.pdf";
      link.click();
      URL.revokeObjectURL(url);
      setError("");
    },
    onError: fail("Не удалось собрать PDF"),
  });

  const pick = (setter: (file: File | null) => void) => (event: React.ChangeEvent<HTMLInputElement>) => {
    setter(event.target.files?.[0] ?? null);
    setPlan(null);
    setError("");
  };
  const busy = checkMutation.isPending || buildMutation.isPending;
  const bothFiles = Boolean(workbook && stickers);

  return (
    <Shell current={AUTOMATION_TITLE}>
      <main className="reviews-content">
        <div className="reviews-heading">
          <div>
            <h1>{AUTOMATION_TITLE}</h1>
            <p className="muted">
              Стикеры коробов FBO-поставки в порядке строк вашего Excel коробовки — чтобы клеить подряд, не
              сверяя каждый короб.
            </p>
          </div>
        </div>

        {error && (
          <div className="inline-error">
            <div>
              <strong>Не получилось</strong>
              {error.split("\n").map((line) => (
                <div key={line}>{line}</div>
              ))}
            </div>
          </div>
        )}

        <section className="box-stickers-panel">
          <ol className="returns-steps">
            <li>
              <span>
                Excel коробовки — тот, что вы заполнили и загрузили в WB. Порядок его строк станет порядком
                стикеров.
              </span>
              <label className="box-stickers-file">
                <span className="field-label">Excel коробовки (.xlsx)</span>
                <input type="file" accept=".xlsx" onChange={pick(setWorkbook)} disabled={busy} />
              </label>
            </li>
            <li>
              <span>PDF со стикерами коробов, скачанный в кабинете WB по этой же поставке.</span>
              <label className="box-stickers-file">
                <span className="field-label">PDF стикеров</span>
                <input type="file" accept=".pdf,application/pdf" onChange={pick(setStickers)} disabled={busy} />
              </label>
            </li>
            <li>
              <label className="box-stickers-check">
                <input type="checkbox" checked={stamp} onChange={(event) => setStamp(event.target.checked)} />
                <span>Допечатать на стикере номер по порядку, артикул и количество</span>
              </label>
              <div className="box-stickers-actions">
                <button
                  className="secondary-button"
                  disabled={!bothFiles || busy}
                  onClick={() => checkMutation.mutate()}
                >
                  <ListChecks size={15} />
                  {checkMutation.isPending ? "Сверяем…" : "Проверить"}
                </button>
                <button
                  className="primary-button"
                  disabled={!bothFiles || busy || (plan !== null && !plan.ready)}
                  onClick={() => buildMutation.mutate()}
                  title="Файл отдаётся, только если каждый стикер сошёлся с Excel и данными WB"
                >
                  <Download size={15} />
                  {buildMutation.isPending ? "Собираем…" : "Скачать PDF"}
                </button>
              </div>
            </li>
          </ol>
        </section>

        {plan && (
          <>
            {plan.problems.length > 0 ? (
              <div className="inline-error">
                <div>
                  <strong>PDF не соберётся, пока не исправить</strong>
                  <ul className="box-stickers-problems">
                    {plan.problems.map((problem) => (
                      <li key={problem}>{problem}</li>
                    ))}
                  </ul>
                </div>
              </div>
            ) : (
              <div className="invite-banner">
                <div>
                  <span className="eyebrow">Всё сходится</span>
                  Поставка {plan.supply_id}, {plan.seller_name}: {plan.boxes.length} коробов, каждый стикер совпал с
                  Excel и данными WB.
                </div>
              </div>
            )}
            {plan.boxes.length > 0 && (
              <table className="registry-table">
                <thead>
                  <tr>
                    <th>№</th>
                    <th>ШК короба</th>
                    <th>Содержимое</th>
                    <th>Шт</th>
                    <th>Стр. в PDF WB</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.boxes.map((box) => (
                    <tr key={box.package_code}>
                      <td>{box.position}</td>
                      <td>
                        <strong>{shortShk(box.shk)}</strong>
                      </td>
                      <td>{contents(box) || "—"}</td>
                      <td>{box.quantity}</td>
                      <td className="muted">{box.page}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </main>
    </Shell>
  );
}
