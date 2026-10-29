import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Download, EyeOff, Plus, RefreshCw, Trash2, Undo2, Upload } from "lucide-react";
import { useMemo, useRef, useState, type CSSProperties } from "react";

import { ApiError } from "../api/http";
import {
  addBoardItems,
  boardNotices,
  daysText,
  downloadBoard,
  fbsText,
  getBoard,
  getFbsWarehouses,
  importBoard,
  importSummary,
  refreshBoard,
  removeBoardItem,
  setBoardItemHidden,
  setFbsWarehouses,
  sortBoardRows,
  updateBoardItem,
  visibleBoardRows,
  type BoardHiddenRow,
  type BoardRow,
  type BoardSortKey,
} from "../features/turnover/board";

const TEMPLATE = "120px 220px 130px 140px 380px 110px 110px 110px 90px 44px";

const message = (error: unknown, fallback: string) => (error instanceof ApiError ? error.message : fallback);

export function TurnoverBoard() {
  const queryClient = useQueryClient();
  const [sellerId, setSellerId] = useState("");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<{ key: BoardSortKey; direction: "asc" | "desc" }>({ key: "days", direction: "asc" });
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  const { data: board, isLoading } = useQuery({
    queryKey: ["turnover-board"],
    queryFn: getBoard,
    refetchInterval: 60_000,
  });
  const { data: warehouses } = useQuery({ queryKey: ["turnover-board-warehouses"], queryFn: getFbsWarehouses });
  const warehouseMutation = useMutation({
    mutationFn: setFbsWarehouses,
    onSuccess: async (result) => {
      queryClient.setQueryData(["turnover-board-warehouses"], result);
      await queryClient.invalidateQueries({ queryKey: ["turnover-board"] });
    },
    onError: (failure: unknown) => setError(message(failure, "Не удалось сохранить склады")),
  });
  const [warehouseSearch, setWarehouseSearch] = useState("");
  const reload = () => queryClient.invalidateQueries({ queryKey: ["turnover-board"] });
  const fail = (fallback: string) => (failure: unknown) => setError(message(failure, fallback));

  const noteMutation = useMutation({
    mutationFn: (payload: { row: BoardRow; note?: string; nomenclature?: string }) =>
      updateBoardItem(payload.row.seller_id, payload.row.barcode, {
        note: payload.note,
        nomenclature: payload.nomenclature,
      }),
    onSuccess: reload,
    onError: fail("Не удалось сохранить"),
  });
  const hideMutation = useMutation({
    mutationFn: (payload: { sellerId: string; barcode: string; hidden: boolean }) =>
      setBoardItemHidden(payload.sellerId, payload.barcode, payload.hidden),
    onSuccess: reload,
    onError: fail("Не удалось изменить строку"),
  });
  const removeMutation = useMutation({
    mutationFn: (row: BoardHiddenRow) => removeBoardItem(row.seller_id, row.barcode),
    onSuccess: reload,
    onError: fail("Не удалось удалить строку"),
  });
  const addMutation = useMutation({
    mutationFn: (text: string) => addBoardItems(sellerId, text),
    onSuccess: async (result) => {
      setDraft("");
      setError("");
      setInfo(
        result.existing > 0
          ? `Добавлено ${result.added}, уже были в таблице: ${result.existing}.`
          : `Добавлено ${result.added}.`,
      );
      await reload();
    },
    onError: fail("Не удалось добавить баркоды"),
  });
  const importMutation = useMutation({
    mutationFn: importBoard,
    onSuccess: async (result) => {
      setError("");
      setInfo(importSummary(result.cabinets));
      await reload();
    },
    onError: fail("Не удалось загрузить список"),
  });
  const refreshMutation = useMutation({
    mutationFn: refreshBoard,
    onSuccess: async (result) => {
      setError("");
      setInfo(
        result.queued > 0
          ? "Обновление поставлено в очередь, цифры обновятся через несколько минут."
          : "Нет кабинетов со строками.",
      );
      await reload();
    },
    onError: fail("Не удалось поставить обновление"),
  });
  const exportMutation = useMutation({
    mutationFn: downloadBoard,
    onSuccess: ({ blob, filename }) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename ?? "oborachivaemost.xlsx";
      link.click();
      URL.revokeObjectURL(url);
    },
    onError: fail("Не удалось собрать Excel"),
  });

  const rows = useMemo(
    () => sortBoardRows(visibleBoardRows(board?.rows ?? [], sellerId, search), sort.key, sort.direction),
    [board?.rows, sellerId, search, sort],
  );
  const hidden = (board?.hidden ?? []).filter((row) => !sellerId || row.seller_id === sellerId);

  if (isLoading || !board) return <div className="loading-block">Загружаем таблицу…</div>;

  const notices = boardNotices(board, sellerId);
  const toggleSort = (key: BoardSortKey) =>
    setSort((current) =>
      current.key === key
        ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
        : { key, direction: key === "days" ? "asc" : "desc" },
    );
  const sortMark = (key: BoardSortKey) =>
    sort.key === key ? sort.direction === "asc" ? <ArrowUp size={12} /> : <ArrowDown size={12} /> : null;

  return (
    <>
      <div className="reviews-heading">
        <div>
          <h1>Хватит на</h1>
          <p className="muted">
            Заказано за {board.window_days} дн. ({board.window_from} — {board.window_to}), как в отчёте WB; остаток —
            остаток WB, как в колонке «Склад WB РФ» отчёта кабинета. Красным — меньше {board.red_days} дн., жёлтым — до {board.yellow_days}. «Остатки ФБС» — только по выбранным складам.
          </p>
        </div>
        <div className="review-sync-actions">
          <button
            className="secondary-button"
            onClick={() => refreshMutation.mutate()}
            disabled={refreshMutation.isPending}
            title="Собрать заказы и остатки прямо сейчас"
          >
            <RefreshCw size={15} className={refreshMutation.isPending ? "spinning" : undefined} />
            Обновить данные
          </button>
          <input
            ref={fileInput}
            type="file"
            accept=".xlsx"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) importMutation.mutate(file);
              event.target.value = "";
            }}
          />
          <button
            className="secondary-button"
            onClick={() => fileInput.current?.click()}
            disabled={importMutation.isPending}
            title="Excel со столбцами «Кабинет», «Баркод», «Номенклатура 1С» (лист «ОСНОВА»). Новые баркоды добавятся, прежние не тронутся."
          >
            <Upload size={15} />
            {importMutation.isPending ? "Загружаем…" : "Загрузить из Excel"}
          </button>
          <button className="primary-button" onClick={() => exportMutation.mutate()} disabled={exportMutation.isPending}>
            <Download size={15} />
            {exportMutation.isPending ? "Собираем…" : "Скачать Excel"}
          </button>
        </div>
      </div>

      {error && <div className="inline-error">{error}</div>}
      {info && !error && <div className="checklist-notice">{info}</div>}
      {notices.map((text) => (
        <div className="checklist-notice" key={text}>
          {text}
        </div>
      ))}

      <div className="checklist-toolbar stocks-toolbar">
        <select aria-label="Кабинет" value={sellerId} onChange={(event) => setSellerId(event.target.value)}>
          <option value="">Все кабинеты</option>
          {board.sellers.map((seller) => (
            <option key={seller.seller_id} value={seller.seller_id}>
              {seller.name}
            </option>
          ))}
        </select>
        <input
          type="search"
          aria-label="Поиск по строкам"
          placeholder="Баркод, артикул, название, номенклатура или комментарий"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>

      {warehouses && (
        <details className="stocks-hidden">
          <summary>
            Склады для «Остатков ФБС» ({warehouses.filter((item) => item.selected).length} из {warehouses.length})
          </summary>
          <p className="muted">
            «Есть» — если на отмеченных складах хотя бы один размер артикула с остатком. Названия одинаковые во всех
            кабинетах. Чтобы расширить список, отметьте нужный склад.
          </p>
          <input
            type="search"
            aria-label="Поиск склада"
            placeholder="Найти склад"
            value={warehouseSearch}
            onChange={(event) => setWarehouseSearch(event.target.value)}
          />
          <ul className="stocks-hidden-list">
            {warehouses
              .filter((item) => item.selected || item.label.toLowerCase().includes(warehouseSearch.trim().toLowerCase()))
              .map((item) => (
                <li key={item.key} className="stocks-hidden-row">
                  <label>
                    <input
                      type="checkbox"
                      checked={item.selected}
                      disabled={warehouseMutation.isPending}
                      onChange={() =>
                        warehouseMutation.mutate(
                          warehouses
                            .filter((other) => (other.key === item.key ? !item.selected : other.selected))
                            .map((other) => other.key),
                        )
                      }
                    />{" "}
                    {item.label}
                  </label>
                  <span className="muted">{item.cabinets > 0 ? `кабинетов: ${item.cabinets}` : "нет в кабинетах"}</span>
                </li>
              ))}
          </ul>
        </details>
      )}

      {board.rows.length === 0 ? (
        <div className="empty-state">
          <h2>В таблице пока нет товаров</h2>
          <p>
            Загрузите список из Excel кнопкой выше (столбцы «Кабинет», «Баркод», «Номенклатура 1С») или выберите
            кабинет и впишите баркоды ниже — заказы и остаток подтянутся сами.
          </p>
        </div>
      ) : (
        <section className="checklist-scroll stocks-scroll" style={{ "--stocks-template": TEMPLATE } as CSSProperties}>
          <div className="stocks-head">
            <button type="button" className="stocks-group-head" onClick={() => toggleSort("days")}>
              Хватит на {sortMark("days")}
            </button>
            <span>Комментарии</span>
            <span>Кабинет</span>
            <span>Баркод</span>
            <span>Товар</span>
            <button type="button" className="stocks-group-head" onClick={() => toggleSort("stock")}>
              Остаток WB {sortMark("stock")}
            </button>
            <button type="button" className="stocks-group-head" onClick={() => toggleSort("orders")}>
              Продажи за {board.window_days} дн. {sortMark("orders")}
            </button>
            <span>Средние в день</span>
            <span>Остатки ФБС</span>
            <span />
          </div>
          {rows.map((row) => (
            <div className="stocks-row" key={`${row.seller_id}:${row.barcode}`}>
              <span className={`stocks-cell cover cover-${row.tone}`}>{daysText(row)}</span>
              <div className="checklist-comment">
                <input
                  key={`${row.barcode}:${row.note}`}
                  aria-label={`Комментарий к ${row.barcode}`}
                  defaultValue={row.note}
                  maxLength={2000}
                  placeholder="—"
                  onBlur={(event) => {
                    if (event.target.value !== row.note) noteMutation.mutate({ row, note: event.target.value });
                  }}
                />
              </div>
              <span>{row.seller_name}</span>
              <span className="stocks-barcode">{row.barcode}</span>
              <span className="stocks-product">
                {row.in_catalog || row.article ? (
                  <>
                    <strong>{row.name || row.vendor_code || "Без названия"}</strong>
                    <em>
                      {row.article && (
                        <a
                          className="wb-article-link"
                          href={`https://www.wildberries.ru/catalog/${row.article}/detail.aspx`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {row.article}
                        </a>
                      )}
                      {row.vendor_code && ` · ${row.vendor_code}`}
                      {row.nomenclature && ` · ${row.nomenclature}`}
                    </em>
                  </>
                ) : (
                  <em className="stocks-unknown">нет в каталоге кабинета</em>
                )}
              </span>
              <span className={`stocks-cell ${row.stock === 0 ? "stocks-zero" : ""}`}>{row.stock ?? "—"}</span>
              <span className="stocks-cell">{row.orders}</span>
              <span className="stocks-cell">{row.rate.toFixed(1)}</span>
              <span className={`stocks-cell ${row.fbs === "no" ? "stocks-zero" : ""}`}>{fbsText[row.fbs]}</span>
              <button
                type="button"
                className="stocks-remove"
                onClick={() => hideMutation.mutate({ sellerId: row.seller_id, barcode: row.barcode, hidden: true })}
                aria-label={`Скрыть ${row.barcode}`}
                title="Скрыть"
              >
                <EyeOff size={13} />
              </button>
            </div>
          ))}
          {rows.length === 0 && <span className="fbs-row-empty">Ничего не нашлось.</span>}
        </section>
      )}

      <form
        className="stocks-add"
        onSubmit={(event) => {
          event.preventDefault();
          if (sellerId && draft.trim()) addMutation.mutate(draft);
        }}
      >
        <textarea
          aria-label="Новые баркоды"
          placeholder={
            sellerId
              ? "Баркоды — по одному в строке или через запятую."
              : "Чтобы добавить баркоды, выберите кабинет в списке выше."
          }
          value={draft}
          rows={2}
          disabled={!sellerId}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button className="primary-button" type="submit" disabled={!sellerId || addMutation.isPending || !draft.trim()}>
          <Plus size={15} />
          {addMutation.isPending ? "Добавляем…" : "Добавить баркоды"}
        </button>
      </form>

      {hidden.length > 0 && (
        <details className="stocks-hidden">
          <summary>Скрытые баркоды ({hidden.length})</summary>
          <ul className="stocks-hidden-list">
            {hidden.map((row) => (
              <li key={`${row.seller_id}:${row.barcode}`} className="stocks-hidden-row">
                <span className="stocks-hidden-barcode">{row.barcode}</span>
                <span className="stocks-hidden-title">
                  {row.seller_name} · {row.name || row.note || "—"}
                </span>
                <button
                  type="button"
                  className="stocks-hidden-action"
                  onClick={() => hideMutation.mutate({ sellerId: row.seller_id, barcode: row.barcode, hidden: false })}
                >
                  <Undo2 size={13} /> Вернуть
                </button>
                <button
                  type="button"
                  className="stocks-hidden-action stocks-hidden-delete"
                  onClick={() => {
                    if (window.confirm(`Удалить ${row.barcode} насовсем вместе с комментарием?`)) {
                      removeMutation.mutate(row);
                    }
                  }}
                >
                  <Trash2 size={13} /> Удалить
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}
