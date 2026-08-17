import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { AppHeader } from "../components/AppHeader";
import { ReviewTimeline } from "../components/ReviewTimeline";
import { SellerSidebar } from "../components/SellerSidebar";
import { getSellerReviewHistory, getSellers } from "../features/reviews/api";

export function ReviewsPage() {
  const { data: sellers = [], isLoading: sellersLoading } = useQuery({ queryKey: ["wb-sellers"], queryFn: getSellers });
  const [sellerId, setSellerId] = useState("");
  useEffect(() => { if (!sellerId && sellers[0]) setSellerId(sellers[0].id); }, [sellerId, sellers]);
  const { data: history, isLoading: historyLoading } = useQuery({ queryKey: ["wb-history", sellerId], queryFn: () => getSellerReviewHistory(sellerId), enabled: Boolean(sellerId) });
  const selectedSeller = sellers.find((seller) => seller.id === sellerId);
  return (
    <div className="app-page reviews-shell">
      <AppHeader current="Wildberries Reviews" />
      <div className="reviews-workspace">
        <SellerSidebar sellers={sellers} selectedId={sellerId} onSelect={setSellerId} />
        <main className="reviews-content">
          <div className="reviews-heading"><div><p className="eyebrow">Wildberries / отзывы</p><h1>Мониторинг отзывов</h1><p className="muted">Ежедневные снимки рейтингов товаров селлера {selectedSeller?.name ?? ""}.</p></div><div className="last-sync"><span className="eyebrow">Последняя синхронизация</span><strong>Сегодня, 14:20</strong></div></div>
          {sellersLoading || historyLoading ? <div className="loading-block">Загружаем данные селлера…</div> : history ? <ReviewTimeline key={sellerId} products={history.products} /> : null}
        </main>
      </div>
    </div>
  );
}
