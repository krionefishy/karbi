import { ChevronsLeftRight, Minus, Plus, Trash2 } from "lucide-react";
import { useMemo, useState, type CSSProperties } from "react";

import { gridTemplate, notices, rowCells, visibleRows } from "../features/fbsStocks/board";
import type { StocksBoard, StocksRow } from "../features/fbsStocks/types";

interface Props {
  board: StocksBoard;
  onNote: (barcode: string, note: string) => void;
  onRemove: (barcode: string) => void;
  onAdd: (text: string) => void;
  adding: boolean;
}

/** Таблица кабинета: заметка, баркод, товар и столбцы по группам. Группы сворачиваются, нули красные. */
export function FbsStocksBoard({ board, onNote, onRemove, onAdd, adding }: Props) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [draft, setDraft] = useState("");
  const rows = useMemo(() => visibleRows(board.rows, search), [board.rows, search]);
  const template = gridTemplate(board.groups, collapsed);
  const warnings = notices(board);

  const toggle = (groupId: string) => {
    const next = new Set(collapsed);
    if (next.has(groupId)) next.delete(groupId);
    else next.add(groupId);
    setCollapsed(next);
  };
  const toggleAll = () => {
    setCollapsed(collapsed.size ? new Set() : new Set(board.groups.map((group) => group.id)));
  };

  return (
    <>
      {warnings.map((text) => (
        <div className="checklist-notice" key={text}>
          {text}
        </div>
      ))}
      <div className="checklist-toolbar stocks-toolbar">
        <input
          type="search"
          aria-label="Поиск по строкам"
          placeholder="Баркод, артикул, название или заметка"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <button className="secondary-button" onClick={toggleAll} disabled={board.groups.length === 0}>
          <ChevronsLeftRight size={15} />
          {collapsed.size ? "Раскрыть группы" : "Свернуть группы"}
        </button>
      </div>
      <section className="checklist-scroll" style={{ "--stocks-template": template } as CSSProperties}>
        <div className="stocks-head">
          <span className="checklist-sticky">Заметка</span>
          <span>Баркод</span>
          <span>Товар</span>
          {board.groups.map((group) => (
            <GroupHeader
              key={group.id}
              title={group.title}
              columns={collapsed.has(group.id) ? [] : group.columns.map((column) => column.name)}
              collapsed={collapsed.has(group.id)}
              onToggle={() => toggle(group.id)}
            />
          ))}
          <span />
        </div>
        {rows.map((row) => (
          <BoardRow
            key={row.barcode}
            row={row}
            board={board}
            collapsed={collapsed}
            onNote={(note) => onNote(row.barcode, note)}
            onRemove={() => onRemove(row.barcode)}
          />
        ))}
        {rows.length === 0 && board.rows.length > 0 && <span className="fbs-row-empty">Ничего не нашлось.</span>}
      </section>
      <form
        className="stocks-add"
        onSubmit={(event) => {
          event.preventDefault();
          if (!draft.trim()) return;
          onAdd(draft);
          setDraft("");
        }}
      >
        <textarea
          aria-label="Новые баркоды"
          placeholder="Баркоды — по одному в строке или через запятую. Остаток по ним подтянется при обновлении."
          value={draft}
          rows={2}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button className="primary-button" type="submit" disabled={adding || !draft.trim()}>
          <Plus size={15} />
          {adding ? "Добавляем…" : "Добавить баркоды"}
        </button>
      </form>
    </>
  );
}

interface GroupHeaderProps {
  title: string;
  columns: string[];
  collapsed: boolean;
  onToggle: () => void;
}

function GroupHeader({ title, columns, collapsed, onToggle }: GroupHeaderProps) {
  return (
    <>
      <button
        type="button"
        className="stocks-group-head"
        onClick={onToggle}
        title={collapsed ? "Раскрыть склады группы" : "Свернуть в один столбец"}
        aria-expanded={!collapsed}
      >
        {collapsed ? <Plus size={12} /> : <Minus size={12} />}
        <span>{title}</span>
      </button>
      {columns.map((name, index) => (
        <span key={`${name}-${index}`} className="stocks-column-head" title={name}>
          {name}
        </span>
      ))}
    </>
  );
}

interface RowProps {
  row: StocksRow;
  board: StocksBoard;
  collapsed: Set<string>;
  onNote: (note: string) => void;
  onRemove: () => void;
}

function BoardRow({ row, board, collapsed, onNote, onRemove }: RowProps) {
  const cells = rowCells(row, board.groups, collapsed);
  return (
    <div className="stocks-row">
      <div className="checklist-comment checklist-sticky">
        <input
          // Ключ с текстом: после ответа сервера поле берёт сохранённое значение.
          key={`${row.barcode}:${row.note}`}
          aria-label={`Заметка к ${row.barcode}`}
          defaultValue={row.note}
          maxLength={2000}
          placeholder="—"
          onBlur={(event) => {
            if (event.target.value !== row.note) onNote(event.target.value);
          }}
        />
      </div>
      <span className="stocks-barcode">{row.barcode}</span>
      <span className="stocks-product">
        {row.in_catalog ? (
          <>
            <strong>{row.title || "Без названия"}</strong>
            <em>
              <a
                className="wb-article-link"
                href={`https://www.wildberries.ru/catalog/${row.article}/detail.aspx`}
                target="_blank"
                rel="noreferrer"
              >
                {row.article}
              </a>
              {row.vendor_code && ` · ${row.vendor_code}`}
            </em>
          </>
        ) : (
          <em className="stocks-unknown">нет в каталоге кабинета</em>
        )}
      </span>
      {cells.map((cell, index) => (
        <span
          key={index}
          className={`stocks-cell ${cell.kind === "group" ? "stocks-cell-group" : ""} ${cell.value === 0 ? "stocks-zero" : ""}`}
        >
          {cell.value}
        </span>
      ))}
      <button type="button" className="stocks-remove" onClick={onRemove} aria-label={`Убрать ${row.barcode} из таблицы`}>
        <Trash2 size={13} />
      </button>
    </div>
  );
}
