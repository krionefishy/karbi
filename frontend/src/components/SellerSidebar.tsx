import { Search } from "lucide-react";
import { useMemo, useState } from "react";

import type { Seller } from "../features/reviews/types";

interface SellerSidebarProps {
  sellers: Seller[];
  selectedId: string;
  onSelect: (sellerId: string) => void;
}

const statusText = { success: "Готово", syncing: "Сбор", error: "Ошибка" } as const;

export function SellerSidebar({ sellers, selectedId, onSelect }: SellerSidebarProps) {
  const [search, setSearch] = useState("");
  const filtered = useMemo(() => sellers.filter((seller) => seller.name.toLowerCase().includes(search.toLowerCase())), [search, sellers]);
  return (
    <aside className="seller-sidebar">
      <div className="seller-search">
        <label className="field-label" htmlFor="seller-search">Поиск селлера</label>
        <div className="search-input"><input id="seller-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Название" /><Search size={16} /></div>
      </div>
      <div className="seller-list">
        {filtered.map((seller) => (
          <button key={seller.id} onClick={() => onSelect(seller.id)} className={`seller-item ${selectedId === seller.id ? "seller-selected" : ""}`}>
            <span className="seller-row"><strong>{seller.name}</strong><span className={`sync-state sync-${seller.sync_status}`}><i />{statusText[seller.sync_status]}</span></span>
            <span className="seller-meta">{seller.product_count} товаров · {seller.sync_status === "error" ? "требуется внимание" : "обновлено сегодня"}</span>
          </button>
        ))}
      </div>
    </aside>
  );
}
