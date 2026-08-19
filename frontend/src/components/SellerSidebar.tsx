import { Pencil, Plus, RefreshCw, Search, Unplug } from "lucide-react";
import { useMemo, useState } from "react";

import type { Seller } from "../features/sellers/types";

interface Props {
  sellers: Seller[];
  selectedId: string;
  onSelect: (id: string) => void;
  onAdd: () => void;
  onEdit: (seller: Seller) => void;
  /** Leaving the automation, not leaving the registry. */
  onDetach: (seller: Seller) => void;
  onRetry: (seller: Seller) => void;
}

const statusText = { queued: "В очереди", success: "Готово", syncing: "Сбор", error: "Ошибка" } as const;

export function SellerSidebar({ sellers, selectedId, onSelect, onAdd, onEdit, onDetach, onRetry }: Props) {
  const [search, setSearch] = useState("");
  const filtered = useMemo(
    () => sellers.filter((seller) => seller.name.toLowerCase().includes(search.toLowerCase())),
    [search, sellers],
  );
  return (
    <aside className="seller-sidebar">
      <div className="seller-search">
        <div className="seller-tools">
          <span className="field-label">Подключены</span>
          <button className="add-seller-button" onClick={onAdd}>
            <Plus size={14} />Подключить
          </button>
        </div>
        <div className="search-input">
          <input
            aria-label="Поиск селлера"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Название"
          />
          <Search size={16} />
        </div>
      </div>
      <div className="seller-list">
        {filtered.map((seller) => (
          <div
            key={seller.id}
            className={`seller-item ${selectedId === seller.id ? "seller-selected" : ""}`}
          >
            <button className="seller-select" onClick={() => onSelect(seller.id)}>
              <span className="seller-row">
                <strong>{seller.name}</strong>
                <span className={`sync-state sync-${seller.catalog_sync_status}`}>
                  <i />{statusText[seller.catalog_sync_status]}
                </span>
              </span>
              <span className="seller-meta">
                {seller.product_count} товаров
                {seller.catalog_sync_error ? ` · ${seller.catalog_sync_error}` : ""}
              </span>
            </button>
            <span className="seller-actions">
              {seller.catalog_sync_status === "error" && (
                <button onClick={() => onRetry(seller)} aria-label="Повторить синхронизацию">
                  <RefreshCw size={13} />
                </button>
              )}
              <button onClick={() => onEdit(seller)} aria-label="Редактировать селлера">
                <Pencil size={13} />
              </button>
              <button onClick={() => onDetach(seller)} aria-label="Отключить от автоматизации">
                <Unplug size={13} />
              </button>
            </span>
          </div>
        ))}
        {!filtered.length && <p className="seller-empty">Нет подключённых селлеров</p>}
      </div>
    </aside>
  );
}
