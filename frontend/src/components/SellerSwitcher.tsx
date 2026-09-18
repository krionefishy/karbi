import { Check, ChevronDown, MoreHorizontal, Pencil, Plus, RefreshCw, Search, Unplug } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { Seller } from "../features/sellers/types";

interface Props {
  sellers: Seller[];
  selectedId: string;
  onSelect: (id: string) => void;
  onAdd: () => void;
  onEdit?: (seller: Seller) => void;
  /** Leaving the automation, not leaving the registry. */
  onDetach: (seller: Seller) => void;
  onRetry: (seller: Seller) => void;
}

const statusText = { queued: "В очереди", success: "Готово", syncing: "Сбор", error: "Ошибка" } as const;

function productLabel(count: number) {
  if (count % 10 === 1 && count % 100 !== 11) return `${count} товар`;
  if ([2, 3, 4].includes(count % 10) && ![12, 13, 14].includes(count % 100)) return `${count} товара`;
  return `${count} товаров`;
}

/** Закрывает выпадающий блок по клику снаружи и по Esc. */
function useDismiss(open: boolean, close: () => void, ref: React.RefObject<HTMLElement | null>) {
  useEffect(() => {
    if (!open) return;
    function onPointer(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) close();
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") close();
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close, ref]);
}

/** Переключатель селлера в верхней панели: список с поиском и статусами,
 * действия над выбранным — в меню рядом. Заменяет боковую колонку. */
export function SellerSwitcher({ sellers, selectedId, onSelect, onAdd, onEdit, onDetach, onRetry }: Props) {
  const [open, setOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [search, setSearch] = useState("");
  const listRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const selected = sellers.find((seller) => seller.id === selectedId);
  const filtered = useMemo(
    () => sellers.filter((seller) => seller.name.toLowerCase().includes(search.toLowerCase())),
    [search, sellers],
  );

  useDismiss(open, () => setOpen(false), listRef);
  useDismiss(menuOpen, () => setMenuOpen(false), menuRef);
  useEffect(() => {
    if (open) searchRef.current?.focus();
    else setSearch("");
  }, [open]);

  return (
    <div className="seller-switcher">
      <div className="seller-switcher-pick" ref={listRef}>
        <button
          className="seller-switcher-trigger"
          onClick={() => setOpen((value) => !value)}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-label="Селлер"
        >
          {selected ? (
            <>
              <i className={`status-dot status-dot-${selected.catalog_sync_status}`} aria-hidden="true" />
              <strong>{selected.name}</strong>
              <small>{productLabel(selected.product_count)}</small>
            </>
          ) : (
            <span className="muted">{sellers.length ? "Выберите селлера" : "Селлеры не подключены"}</span>
          )}
          <ChevronDown size={16} aria-hidden="true" />
        </button>
        {open && (
          <div className="popover seller-switcher-menu">
            <label className="popover-search">
              <Search size={16} aria-hidden="true" />
              <input
                ref={searchRef}
                aria-label="Поиск селлера"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Название"
              />
            </label>
            <div className="popover-list" role="listbox" aria-label="Подключённые селлеры">
              {filtered.map((seller) => (
                <button
                  key={seller.id}
                  role="option"
                  aria-selected={seller.id === selectedId}
                  className={`popover-option ${seller.id === selectedId ? "popover-option-selected" : ""}`}
                  onClick={() => {
                    onSelect(seller.id);
                    setOpen(false);
                  }}
                >
                  <i className={`status-dot status-dot-${seller.catalog_sync_status}`} aria-hidden="true" />
                  <span className="popover-option-text">
                    <strong>{seller.name}</strong>
                    <small>
                      {productLabel(seller.product_count)} · {statusText[seller.catalog_sync_status]}
                      {seller.catalog_sync_error ? ` · ${seller.catalog_sync_error}` : ""}
                    </small>
                  </span>
                  {seller.id === selectedId && <Check size={16} aria-hidden="true" />}
                </button>
              ))}
              {!filtered.length && <p className="popover-empty">Нет подключённых селлеров</p>}
            </div>
            <button
              className="popover-action"
              onClick={() => {
                setOpen(false);
                onAdd();
              }}
            >
              <Plus size={16} aria-hidden="true" />
              Подключить селлера
            </button>
          </div>
        )}
      </div>
      {selected && (
        <div className="seller-switcher-actions" ref={menuRef}>
          <button
            className="ghost-button icon-button"
            onClick={() => setMenuOpen((value) => !value)}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label="Действия с селлером"
          >
            <MoreHorizontal size={18} />
          </button>
          {menuOpen && (
            <div className="popover popover-menu" role="menu">
              {selected.catalog_sync_status === "error" && (
                <button
                  role="menuitem"
                  className="popover-option"
                  onClick={() => {
                    setMenuOpen(false);
                    onRetry(selected);
                  }}
                >
                  <RefreshCw size={16} aria-hidden="true" />
                  Повторить синхронизацию
                </button>
              )}
              {onEdit && (
                <button
                  role="menuitem"
                  className="popover-option"
                  onClick={() => {
                    setMenuOpen(false);
                    onEdit(selected);
                  }}
                >
                  <Pencil size={16} aria-hidden="true" />
                  Редактировать селлера
                </button>
              )}
              <button
                role="menuitem"
                className="popover-option popover-option-danger"
                onClick={() => {
                  setMenuOpen(false);
                  onDetach(selected);
                }}
              >
                <Unplug size={16} aria-hidden="true" />
                Отключить от автоматизации
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
