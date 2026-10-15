import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, ListChecks } from "lucide-react";
import { useState } from "react";

import { ApiError } from "../api/http";
import { Shell } from "../components/Shell";
import {
  buildPacking,
  buildStickers,
  checkPacking,
  checkStickers,
  fetchRecentPackings,
} from "../features/boxStickers/api";
import type {
  BoxStickerBox,
  BoxStickerPlan,
  PackingPlan,
  RecentPacking,
  WorkbookSource,
} from "../features/boxStickers/types";

const AUTOMATION_TITLE = "Стикеры коробов";
const MANUAL = "file";

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

function savedWhen(iso: string) {
  return new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function Problems({ title, problems }: { title: string; problems: string[] }) {
  return (
    <div className="inline-error">
      <div>
        <strong>{title}</strong>
        <ul className="box-stickers-problems">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function ErrorBanner({ error }: { error: string }) {
  if (!error) return null;
  return (
    <div className="inline-error">
      <div>
        <strong>Не получилось</strong>
        {error.split("\n").map((line) => (
          <div key={line}>{line}</div>
        ))}
      </div>
    </div>
  );
}

/** Шаг 1: коробовка из таблицы сборки — шаблон WB + вставленные строки → заполненный шаблон. */
function PackingPanel({ onBuilt }: { onBuilt: (supplyId: number | null) => void }) {
  const [template, setTemplate] = useState<File | null>(null);
  const [assembly, setAssembly] = useState("");
  const [plan, setPlan] = useState<PackingPlan | null>(null);
  const [error, setError] = useState("");
  const fail = (fallback: string) => (reason: unknown) =>
    setError(reason instanceof ApiError ? reason.message : fallback);

  const checkMutation = useMutation({
    mutationFn: () => checkPacking(template as File, assembly),
    onSuccess: (result) => {
      setError("");
      setPlan(result);
    },
    onError: fail("Не удалось разложить короба"),
  });
  const buildMutation = useMutation({
    mutationFn: () => buildPacking(template as File, assembly),
    onSuccess: ({ blob, filename }) => {
      download(blob, filename ?? "packing.xlsx");
      setError("");
      onBuilt(plan?.supply_id ?? null);
    },
    onError: fail("Не удалось заполнить коробовку"),
  });
  const busy = checkMutation.isPending || buildMutation.isPending;
  const filled = Boolean(template && assembly.trim());

  return (
    <section className="box-stickers-panel">
      <h2 className="box-stickers-title">1. Заполнить коробовку из таблицы сборки</h2>
      <ErrorBanner error={error} />
      <ol className="returns-steps">
        <li>
          <span>
            Шаблон коробовки из кабинета WB — скачанный сразу после создания коробов, с пустыми баркодами.
          </span>
          <label className="box-stickers-file">
            <span className="field-label">Шаблон коробовки (.xlsx)</span>
            <input
              type="file"
              accept=".xlsx"
              onChange={(event) => {
                setTemplate(event.target.files?.[0] ?? null);
                setPlan(null);
                setError("");
              }}
              disabled={busy}
            />
          </label>
        </li>
        <li>
          <span>
            Строки этой поставки из таблицы сборки: выделите их в Google-таблице целиком, скопируйте и
            вставьте сюда. Порядок строк станет порядком коробов.
          </span>
          <label className="box-stickers-file">
            <span className="field-label">Строки поставки</span>
            <textarea
              className="box-stickers-text"
              value={assembly}
              onChange={(event) => {
                setAssembly(event.target.value);
                setPlan(null);
                setError("");
              }}
              placeholder={"41498020\t1304218079\t2053998817899\t1-6\tЗарядное устройство\t290\t290\t290\t5-50+1-40"}
              disabled={busy}
              rows={6}
              spellCheck={false}
            />
          </label>
        </li>
        <li>
          <div className="box-stickers-actions">
            <button className="secondary-button" disabled={!filled || busy} onClick={() => checkMutation.mutate()}>
              <ListChecks size={15} />
              {checkMutation.isPending ? "Раскладываем…" : "Проверить"}
            </button>
            <button
              className="primary-button"
              disabled={!filled || busy || (plan !== null && !plan.ready)}
              onClick={() => buildMutation.mutate()}
              title="Файл отдаётся, только если строки сошлись с шаблоном и с товарами поставки в WB"
            >
              <Download size={15} />
              {buildMutation.isPending ? "Заполняем…" : "Скачать коробовку"}
            </button>
          </div>
          <span className="muted">
            Скачанный файл загрузите в кабинет WB и скачайте стикеры. Коробовку мы запомним: на шаге 2 её
            загружать не нужно.
          </span>
        </li>
      </ol>

      {plan && (
        <>
          {plan.problems.length > 0 ? (
            <Problems title="Коробовка не заполнится, пока не исправить" problems={plan.problems} />
          ) : (
            <div className="invite-banner">
              <div>
                <span className="eyebrow">Всё сходится</span>
                Поставка {plan.supply_id}, {plan.seller_name}: {plan.boxes.length} коробов, баркоды и количество
                совпали с поставкой в WB.
              </div>
            </div>
          )}
          {plan.boxes.length > 0 && (
            <table className="registry-table">
              <thead>
                <tr>
                  <th>№</th>
                  <th>ШК короба</th>
                  <th>Баркод</th>
                  <th>Шт</th>
                </tr>
              </thead>
              <tbody>
                {plan.boxes.map((box) => (
                  <tr key={box.package_code}>
                    <td>{box.position}</td>
                    <td>
                      <strong>{shortShk(box.shk)}</strong>
                    </td>
                    <td>{box.barcode}</td>
                    <td>{box.quantity}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  );
}

/** Шаг 2: стикеры по порядку — коробовка (по номеру поставки или файлом) + PDF стикеров → PDF. */
function StickersPanel({
  recent,
  selected,
  onSelect,
}: {
  recent: RecentPacking[];
  selected: string;
  onSelect: (value: string) => void;
}) {
  const [workbookFile, setWorkbookFile] = useState<File | null>(null);
  const [stickers, setStickers] = useState<File | null>(null);
  const [plan, setPlan] = useState<BoxStickerPlan | null>(null);
  const [error, setError] = useState("");
  const fail = (fallback: string) => (reason: unknown) =>
    setError(reason instanceof ApiError ? reason.message : fallback);

  const source: WorkbookSource | null =
    selected === MANUAL
      ? workbookFile && { kind: "file", file: workbookFile }
      : { kind: "supply", supplyId: Number(selected) };

  const checkMutation = useMutation({
    mutationFn: () => checkStickers(source as WorkbookSource, stickers as File),
    onSuccess: (result) => {
      setError("");
      setPlan(result);
    },
    onError: fail("Не удалось сверить файлы"),
  });
  const buildMutation = useMutation({
    mutationFn: () => buildStickers(source as WorkbookSource, stickers as File),
    onSuccess: ({ blob, filename }) => {
      download(blob, filename ?? "box_stickers.pdf");
      setError("");
    },
    onError: fail("Не удалось собрать PDF"),
  });
  const reset = () => {
    setPlan(null);
    setError("");
  };
  const busy = checkMutation.isPending || buildMutation.isPending;
  const ready = Boolean(source && stickers);

  return (
    <section className="box-stickers-panel">
      <h2 className="box-stickers-title">2. Разложить стикеры по порядку</h2>
      <ErrorBanner error={error} />
      <ol className="returns-steps">
        <li>
          <span>
            Коробовка поставки: та, что заполнили на шаге 1, или Excel, который вы загрузили в WB сами.
            Порядок её строк станет порядком стикеров.
          </span>
          <label className="box-stickers-file">
            <span className="field-label">Коробовка</span>
            <select
              className="box-stickers-select"
              value={selected}
              onChange={(event) => {
                onSelect(event.target.value);
                reset();
              }}
              disabled={busy}
            >
              {recent.map((item) => (
                <option key={item.supply_id} value={String(item.supply_id)}>
                  Поставка {item.supply_id} · {item.seller_name} · {item.boxes} коробов · {savedWhen(item.saved_at)}
                </option>
              ))}
              <option value={MANUAL}>Загрузить Excel файлом</option>
            </select>
          </label>
          {selected === MANUAL && (
            <label className="box-stickers-file">
              <span className="field-label">Excel коробовки (.xlsx)</span>
              <input
                type="file"
                accept=".xlsx"
                onChange={(event) => {
                  setWorkbookFile(event.target.files?.[0] ?? null);
                  reset();
                }}
                disabled={busy}
              />
            </label>
          )}
        </li>
        <li>
          <span>PDF со стикерами коробов, скачанный в кабинете WB по этой же поставке.</span>
          <label className="box-stickers-file">
            <span className="field-label">PDF стикеров</span>
            <input
              type="file"
              accept=".pdf,application/pdf"
              onChange={(event) => {
                setStickers(event.target.files?.[0] ?? null);
                reset();
              }}
              disabled={busy}
            />
          </label>
        </li>
        <li>
          <div className="box-stickers-actions">
            <button className="secondary-button" disabled={!ready || busy} onClick={() => checkMutation.mutate()}>
              <ListChecks size={15} />
              {checkMutation.isPending ? "Сверяем…" : "Проверить"}
            </button>
            <button
              className="primary-button"
              disabled={!ready || busy || (plan !== null && !plan.ready)}
              onClick={() => buildMutation.mutate()}
              title="Файл отдаётся, только если каждый стикер сошёлся с Excel и данными WB"
            >
              <Download size={15} />
              {buildMutation.isPending ? "Собираем…" : "Скачать PDF"}
            </button>
          </div>
        </li>
      </ol>

      {plan && (
        <>
          {plan.problems.length > 0 ? (
            <Problems title="PDF не соберётся, пока не исправить" problems={plan.problems} />
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
    </section>
  );
}

export function BoxStickersPage() {
  const queryClient = useQueryClient();
  const recentQuery = useQuery({ queryKey: ["box-stickers", "recent"], queryFn: fetchRecentPackings });
  const recent = recentQuery.data ?? [];
  // Пока менеджер не выбрал сам, подставляется последняя заполненная коробовка.
  const [chosen, setChosen] = useState<string | null>(null);
  const selected = chosen ?? (recent[0] ? String(recent[0].supply_id) : MANUAL);

  return (
    <Shell current={AUTOMATION_TITLE}>
      <main className="reviews-content">
        <div className="reviews-heading">
          <div>
            <h1>{AUTOMATION_TITLE}</h1>
            <p className="muted">
              Коробовка FBO-поставки заполняется из таблицы сборки, а стикеры коробов раскладываются в её
              порядке — чтобы клеить подряд, не сверяя каждый короб.
            </p>
          </div>
        </div>

        <details className="box-stickers-help">
          <summary>Как работает инструмент</summary>
          <div className="box-stickers-help-body">
            <p>
              Инструмент заполняет коробовку FBO-поставки по таблице сборки склада и раскладывает стикеры
              коробов в её порядке. Данные в кабинет WB он не загружает: файл скачивается и загружается в
              кабинет вручную.
            </p>
            <h3>Шаг 1. Коробовка из таблицы сборки</h3>
            <ol>
              <li>
                В кабинете WB создаётся поставка и короба, затем скачивается шаблон коробовки («Через Excel» →
                «Скачать шаблон»). Нужен пустой шаблон: с кодами коробов и без баркодов.
              </li>
              <li>Шаблон загружается в поле «Шаблон коробовки».</li>
              <li>
                В Google-таблице сборки выделяются строки этой поставки целиком и вставляются в поле «Строки
                поставки». Колонки распознаются по содержимому: номер поставки, баркод, количество в сборке и
                схема коробов вида «3-60+1-40» (три короба по 60 и один на 40). Ячейка, которую таблица
                превратила в дату («01.04.2026»), читается как «1-4». Строки без номера поставки
                пропускаются.
              </li>
              <li>
                «Проверить» показывает раскладку: короба идут по порядку строк таблицы, первый короб получает
                первую строку шаблона. «Скачать коробовку» отдаёт тот же шаблон с заполненными баркодами и
                количеством; коды коробов не меняются.
              </li>
              <li>
                Файл загружается в кабинет WB («Через Excel» → «Загрузить файл»), после чего в кабинете
                скачиваются стикеры коробов.
              </li>
            </ol>
            <h3>Шаг 2. Стикеры по порядку</h3>
            <ol>
              <li>
                В списке «Коробовка» выбирается поставка, заполненная на шаге 1. Коробовка хранится 30 дней.
                Если коробовку заполняли не здесь, выбирается «Загрузить Excel файлом».
              </li>
              <li>
                Загружается PDF стикеров из кабинета. «Проверить» сопоставляет стикеры с коробовкой и
                упаковкой поставки в WB, «Скачать PDF» отдаёт стикеры в порядке строк коробовки. Страницы WB
                не изменяются, только переставляются.
              </li>
            </ol>
            <h3>Что проверяется до выдачи файла</h3>
            <ul>
              <li>Сумма по схеме равна количеству в строке; у каждой строки с номером поставки есть схема.</li>
              <li>Коробов в шаблоне ровно столько, сколько по схемам. Иначе сообщается, сколько добавить или удалить в кабинете.</li>
              <li>Баркоды и количество совпадают с товарами поставки в WB; товар, заявленный в поставке, есть в строках.</li>
              <li>Стикер каждого короба найден в PDF, количество на стикере совпадает с упаковкой в WB.</li>
            </ul>
            <p>
              Не поддерживаются короба с несколькими товарами и срок годности: такие строки дописываются в
              скачанный файл вручную с тем же ШК короба.
            </p>
          </div>
        </details>

        <PackingPanel
          onBuilt={(supplyId) => {
            void queryClient.invalidateQueries({ queryKey: ["box-stickers", "recent"] });
            if (supplyId) setChosen(String(supplyId));
          }}
        />
        <StickersPanel recent={recent} selected={selected} onSelect={setChosen} />
      </main>
    </Shell>
  );
}
