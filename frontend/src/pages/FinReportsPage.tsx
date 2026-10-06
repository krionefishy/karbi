import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Plus, Unplug, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ApiError } from "../api/http";
import { ConnectSellerDialog } from "../components/ConnectSellerDialog";
import { ConfirmDialog } from "../components/SellerDialog";
import { Shell } from "../components/Shell";
import { downloadPnl, getPnl, uploadCosts } from "../features/finReports/api";
import { articles, defaultPeriod, money, reports, uploadSummary } from "../features/finReports/format";
import type { CostUploadResult, Granularity, PnlLine, PnlValues } from "../features/finReports/types";
import { attachSeller, detachSeller, getAutomationSellers, getSellers } from "../features/sellers/api";
import type { Seller, SellerInput } from "../features/sellers/types";

const AUTOMATION_ID = "fin-reports";
const AUTOMATION_TITLE = "Финансовые отчёты";
const FIRST_YEAR = 2026;

const GRANULARITIES: { value: Granularity; label: string }[] = [
  { value: "week", label: "Недели" },
  { value: "month", label: "Месяцы" },
];

const momentFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" });
const dayFormatter = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
const stamp = (value: string | null) => (value ? momentFormatter.format(new Date(value)) : "ещё не собирались");

interface Column {
  key: string;
  title: string;
  values: PnlValues | undefined;
  /** Отчёт WB по колонке дочитан не весь: цифры неполные. */
  partial?: boolean;
  /** Итог года: колонка не период, выбрать её нельзя. */
  fixed?: boolean;
}

/** Строки ОПиУ сверху вниз, по колонке на период или кабинет. */
function PnlTable({
  lines,
  columns,
  selected,
  onSelect,
  label,
}: {
  lines: PnlLine[];
  columns: Column[];
  selected?: string;
  onSelect?: (key: string) => void;
  label: string;
}) {
  const template = `minmax(280px, 340px) repeat(${columns.length}, minmax(150px, 1fr))`;
  return (
    <section
      className="checklist-scroll stocks-scroll fin-table"
      style={{ "--stocks-template": template } as React.CSSProperties}
      aria-label={label}
    >
      <div className="stocks-head">
        <span className="checklist-sticky">Статья</span>
        {columns.map((column) =>
          onSelect && !column.fixed ? (
            <button
              key={column.key}
              type="button"
              className={column.key === selected ? "fin-period fin-period-active" : "fin-period"}
              onClick={() => onSelect(column.key)}
              title={column.partial ? "Отчёт WB дочитан не по всем кабинетам" : "Показать период по кабинетам"}
            >
              {column.title}
              {column.partial && <small>неполный</small>}
            </button>
          ) : (
            <span key={column.key} className="fin-head-cell">
              {column.title}
              {column.partial && <small>неполный</small>}
            </span>
          ),
        )}
      </div>
      {lines.map((line) => (
        <div className={`stocks-row fin-row fin-row-${line.level}`} key={line.key}>
          <span className="checklist-sticky">{line.title}</span>
          {columns.map((column) => (
            <span key={column.key} className="stocks-cell fin-cell">
              {money(column.values?.[line.key])}
            </span>
          ))}
        </div>
      ))}
    </section>
  );
}

export function FinReportsPage() {
  const queryClient = useQueryClient();
  const currentYear = new Date().getFullYear();
  const [year, setYear] = useState(currentYear);
  const [granularity, setGranularity] = useState<Granularity>("week");
  const [sellerId, setSellerId] = useState("");
  const [period, setPeriod] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [detaching, setDetaching] = useState<Seller | null>(null);
  const [formError, setFormError] = useState("");
  const [actionError, setActionError] = useState("");
  const [uploaded, setUploaded] = useState<CostUploadResult | null>(null);
  const [costsFrom, setCostsFrom] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  const { data: sellers = [] } = useQuery({
    queryKey: ["automation-sellers", AUTOMATION_ID],
    queryFn: () => getAutomationSellers(AUTOMATION_ID),
  });
  const { data: registry = [] } = useQuery({
    queryKey: ["wb-sellers", false],
    queryFn: () => getSellers(),
    enabled: connecting,
  });
  const {
    data: pnl,
    isLoading,
    isPlaceholderData: pnlStale,
    error: loadError,
  } = useQuery({
    queryKey: ["fin-reports", year, granularity, sellerId],
    queryFn: () => getPnl(year, granularity, sellerId),
    placeholderData: (previous) => previous,
  });

  // Выбранный кабинет могли отключить, а период — сменить вместе с разрезом.
  useEffect(() => {
    if (sellerId && !sellers.some((item) => item.id === sellerId)) setSellerId("");
  }, [sellerId, sellers]);
  useEffect(() => {
    if (pnl && !pnl.periods.some((item) => item.key === period)) setPeriod(defaultPeriod(pnl.periods));
  }, [pnl, period]);

  const fail = (fallback: string) => (error: unknown) =>
    setActionError(error instanceof ApiError ? error.message : fallback);
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["fin-reports"] });
  };
  const refreshSellers = async () => {
    await queryClient.invalidateQueries({ queryKey: ["automation-sellers", AUTOMATION_ID] });
    await queryClient.invalidateQueries({ queryKey: ["wb-sellers"] });
    await refresh();
  };

  const exportMutation = useMutation({
    mutationFn: () => downloadPnl(year, period),
    onSuccess: ({ blob, filename }) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename ?? "pnl.xlsx";
      link.click();
      URL.revokeObjectURL(url);
      setActionError("");
    },
    onError: fail("Не удалось выгрузить книгу"),
  });
  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadCosts(file, costsFrom),
    onSuccess: async (result) => {
      setUploaded(result);
      setActionError("");
      await refresh();
    },
    onError: fail("Не удалось загрузить себестоимость"),
    onSettled: () => {
      // Тот же файл можно выбрать снова — иначе повторная загрузка не сработает.
      if (fileInput.current) fileInput.current.value = "";
    },
  });
  const connectMutation = useMutation({
    mutationFn: (payload: { seller_id: string } | SellerInput) => attachSeller(AUTOMATION_ID, payload),
    onSuccess: async () => {
      setConnecting(false);
      setFormError("");
      await refreshSellers();
    },
    onError: (error) => setFormError(error instanceof ApiError ? error.message : "Не удалось подключить селлера"),
  });
  const detachMutation = useMutation({
    mutationFn: (seller: Seller) => detachSeller(AUTOMATION_ID, seller.id),
    onSuccess: async () => {
      setDetaching(null);
      await refreshSellers();
    },
  });

  const chosen = pnl?.periods.find((item) => item.key === period);
  const pending = (pnl?.sellers ?? []).filter((state) => state.pending_reports > 0);
  const failing = (pnl?.sellers ?? []).filter((state) => state.error);
  const uncostedRevenue = (pnl?.uncosted ?? []).reduce((sum, item) => sum + item.revenue, 0);
  const years = Array.from({ length: currentYear - FIRST_YEAR + 1 }, (_, index) => currentYear - index);

  return (
    <Shell current={AUTOMATION_TITLE}>
      <main className="reviews-content">
        <div className="reviews-heading">
          <div>
            <h1>{AUTOMATION_TITLE}</h1>
            <p className="muted">
              ОПиУ по отчётам реализации Wildberries: недели и месяцы, сводно и по каждому кабинету. Цифры считаются
              из отчётов WB, себестоимость — из загруженного файла.
            </p>
          </div>
          <div className="review-sync-actions">
            <button
              className="primary-button"
              disabled={exportMutation.isPending || !pnl || pnlStale || pnl.periods.length === 0}
              onClick={() => exportMutation.mutate()}
              title="Выбранный период по кабинетам, недели и месяцы года, артикулы без себестоимости"
            >
              <Download size={15} />
              {exportMutation.isPending ? "Собираем…" : "Выгрузить в Excel"}
            </button>
          </div>
        </div>

        {actionError && <div className="inline-error">{actionError}</div>}
        {loadError && (
          <div className="inline-error">
            {loadError instanceof ApiError ? loadError.message : "Не удалось загрузить отчёт"}
          </div>
        )}
        {failing.map((state) => (
          <div key={state.seller_id} className="checklist-notice">
            {state.name}: отчёты WB не читаются — {state.error}
          </div>
        ))}
        {pending.length > 0 && (
          <div className="checklist-notice">
            Отчёты WB ещё дочитываются:{" "}
            {pending.map((state) => `${state.name} — ${reports(state.pending_reports)}`).join(", ")}. Периоды с пометкой
            «неполный» досчитаются сами.
          </div>
        )}
        {pnl && pnl.uncosted.length > 0 && (
          <details className="checklist-notice fin-uncosted">
            <summary>
              Без себестоимости {articles(pnl.uncosted.length)} с выручкой {money(uncostedRevenue)} ₽ за год —
              себестоимость и маржа по ним не посчитаны.
            </summary>
            <ul>
              {pnl.uncosted.map((item) => (
                <li key={`${item.seller_id}:${item.nm_id}`}>
                  {item.seller_name} · {item.nm_id} · {item.vendor_code || "без артикула продавца"} —{" "}
                  {money(item.revenue)} ₽
                </li>
              ))}
            </ul>
          </details>
        )}

        {!pnl ? (
          // Ошибка первой загрузки уже показана выше: «Загружаем…» рядом с ней висело бы вечно.
          isLoading && <div className="loading-block">Загружаем данные…</div>
        ) : sellers.length === 0 ? (
          <div className="empty-state">
            <h2>Подключите первый кабинет</h2>
            <p>
              Отчёты реализации WB собираются для всех кабинетов с начала года. После подключения кабинет появится в
              отчёте сразу — с теми неделями, что уже дочитаны.
            </p>
            <button className="primary-button" onClick={() => setConnecting(true)}>
              Подключить селлера
            </button>
          </div>
        ) : (
          <>
            <div className="checklist-toolbar fin-toolbar">
              <div className="mode-switch" role="tablist" aria-label="Разрез отчёта">
                {GRANULARITIES.map((item) => (
                  <button
                    key={item.value}
                    role="tab"
                    aria-selected={granularity === item.value}
                    className={granularity === item.value ? "mode-active" : undefined}
                    onClick={() => setGranularity(item.value)}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <label className="penalties-field">
                <span className="field-label">Год</span>
                <select value={year} onChange={(event) => setYear(Number(event.target.value))}>
                  {years.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>
              <label className="penalties-field">
                <span className="field-label">Кабинет</span>
                <select value={sellerId} onChange={(event) => setSellerId(event.target.value)}>
                  <option value="">Все кабинеты</option>
                  {sellers.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {pnl.periods.length === 0 ? (
              <div className="empty-state">
                <h2>Отчётов за {pnl.year} год пока нет</h2>
                <p>Зеркало отчётов реализации ещё не дошло до подключённых кабинетов — обычно это вопрос часа.</p>
              </div>
            ) : (
              <>
                <PnlTable
                  label="ОПиУ по периодам"
                  lines={pnl.lines}
                  selected={period}
                  onSelect={setPeriod}
                  columns={[
                    { key: "total", title: `${pnl.year} ИТОГО`, values: pnl.total, fixed: true },
                    ...pnl.periods.map((item) => ({
                      key: item.key,
                      title: item.label,
                      values: item.values,
                      partial: item.pending_sellers.length > 0,
                    })),
                  ]}
                />
                {chosen && pnl.sellers.length > 1 && (
                  <>
                    <h2 className="fin-subtitle">
                      {chosen.label} по кабинетам
                      <small className="muted">
                        {" "}
                        {dayFormatter.format(new Date(`${chosen.date_from}T00:00:00`))}–
                        {dayFormatter.format(new Date(`${chosen.date_to}T00:00:00`))}
                      </small>
                    </h2>
                    <PnlTable
                      label="ОПиУ выбранного периода по кабинетам"
                      lines={pnl.lines}
                      columns={[
                        { key: "total", title: "WB итого", values: chosen.values },
                        ...pnl.sellers.map((state) => ({
                          key: state.seller_id,
                          title: state.name,
                          values: chosen.by_seller[state.seller_id],
                          partial: chosen.pending_sellers.includes(state.seller_id),
                        })),
                      ]}
                    />
                  </>
                )}
              </>
            )}

            <section className="card fin-costs">
              <div className="checklist-toolbar stocks-toolbar">
                <strong>Себестоимость</strong>
                <input
                  ref={fileInput}
                  type="file"
                  accept=".xlsx"
                  hidden
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) uploadMutation.mutate(file);
                  }}
                />
                <label className="penalties-field fin-costs-from">
                  <span className="field-label">Действует с</span>
                  <input
                    type="date"
                    value={costsFrom}
                    max={new Date().toISOString().slice(0, 10)}
                    onChange={(event) => setCostsFrom(event.target.value)}
                  />
                </label>
                <button
                  className="secondary-button"
                  disabled={uploadMutation.isPending}
                  onClick={() => fileInput.current?.click()}
                >
                  <Upload size={15} /> {uploadMutation.isPending ? "Загружаем…" : "Загрузить файл"}
                </button>
              </div>
              <p className="muted">
                Excel с колонками «Кабинет», «АртикулВБ» и «Себес, руб». Без даты новая цена действует с дня загрузки
                и прошлые периоды не меняет; цена, которая не изменилась, заново не записывается. Чтобы исправить
                ошибку, загрузите файл с датой, с которой цена должна действовать.
              </p>
              {uploaded && (
                <div className="fin-upload-result" role="status">
                  <strong>{uploadSummary(uploaded)}</strong>
                  {uploaded.unknown_cabinets.length > 0 && (
                    <p>Кабинеты из файла, которых нет в реестре: {uploaded.unknown_cabinets.join(", ")}.</p>
                  )}
                  {uploaded.problems.length > 0 && (
                    <ul>
                      {uploaded.problems.map((problem) => (
                        <li key={problem}>{problem}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </section>

            <section className="card podsort-sellers">
              <div className="checklist-toolbar stocks-toolbar">
                <strong>Кабинеты</strong>
                <button className="secondary-button" onClick={() => setConnecting(true)}>
                  <Plus size={15} /> Подключить кабинет
                </button>
              </div>
              {sellers.map((seller) => {
                const state = pnl.sellers.find((item) => item.seller_id === seller.id);
                return (
                  <div key={seller.id} className="stocks-warehouse-row podsort-seller-row fin-seller-row">
                    <span>
                      <strong>{seller.name}</strong>
                      {state && (
                        <em className="muted">
                          {" "}
                          · {reports(state.reports)} за год
                          {state.pending_reports > 0 && `, дочитывается ${state.pending_reports}`}
                        </em>
                      )}
                    </span>
                    {/* При фильтре по одному кабинету про остальные сервер не рассказывает — молчим. */}
                    <span className="muted">{state ? `Отчёты WB: ${stamp(state.collected_at)} · собрано: ${stamp(state.built_at)}` : ""}</span>
                    <button
                      className="secondary-button stocks-hidden-action"
                      onClick={() => setDetaching(seller)}
                      title="Отключить кабинет от финансовых отчётов"
                    >
                      <Unplug size={14} />
                    </button>
                  </div>
                );
              })}
            </section>
          </>
        )}
      </main>

      {connecting && (
        <ConnectSellerDialog
          automationTitle={AUTOMATION_TITLE}
          available={registry.filter((item) => !sellers.some((enrolled) => enrolled.id === item.id))}
          pending={connectMutation.isPending}
          error={formError}
          onClose={() => setConnecting(false)}
          onConnect={(payload) => connectMutation.mutate(payload)}
        />
      )}
      {detaching && (
        <ConfirmDialog
          title="Отключить от финансовых отчётов?"
          description={`«${detaching.name}» пропадёт из отчёта. Загруженная себестоимость останется до повторного подключения.`}
          confirmLabel="Отключить"
          pendingLabel="Отключаем…"
          pending={detachMutation.isPending}
          onClose={() => setDetaching(null)}
          onConfirm={() => detachMutation.mutate(detaching)}
        />
      )}
    </Shell>
  );
}
