import type { Automation } from "../features/automations/types";
import type { ProductReviewHistory, RatingCounts, Seller } from "../features/reviews/types";

export const automations: Automation[] = [
  {
    id: "wb-reviews",
    title: "Мониторинг отзывов Wildberries",
    description: "Ежедневные снимки отзывов по всем товарам и селлерам Wildberries.",
    status: "active",
    last_run_at: "2026-08-17T06:10:00+03:00",
    seller_count: 4,
  },
  {
    id: "marketplace-reports",
    title: "Сводные отчёты маркетплейсов",
    description: "Единая отчётность по продажам и остаткам.",
    status: "coming_soon",
    last_run_at: null,
    seller_count: null,
  },
  {
    id: "ozon-reviews",
    title: "Мониторинг отзывов Ozon",
    description: "Динамика рейтингов и отзывов по товарам Ozon.",
    status: "coming_soon",
    last_run_at: null,
    seller_count: null,
  },
];

export const sellers: Seller[] = [
  { id: "fashion-house", name: "ООО «Модный Дом»", product_count: 124, sync_status: "success", last_sync_at: "2026-08-17T14:20:05+03:00" },
  { id: "kuznetsova", name: "ИП Кузнецова А.М.", product_count: 48, sync_status: "syncing", last_sync_at: "2026-08-17T12:12:00+03:00" },
  { id: "smart-tech", name: "SmartTech Retail", product_count: 215, sync_status: "error", last_sync_at: "2026-08-17T14:15:00+03:00" },
  { id: "eco-product", name: "ЭкоПродукт Групп", product_count: 89, sync_status: "success", last_sync_at: "2026-08-17T13:35:00+03:00" },
];

const dates = Array.from({ length: 21 }, (_, index) => {
  const date = new Date(Date.UTC(2026, 6, 28 + index));
  return date.toISOString().slice(0, 10);
});

function buildSnapshots(start: RatingCounts, changes: number[]) {
  const current = { ...start };
  return dates.map((date, index) => {
    current[5] += changes[index % changes.length];
    current[4] += index % 3 === 0 ? 1 : 0;
    current[3] += index % 5 === 0 ? 1 : 0;
    current[2] += index % 7 === 0 ? 1 : 0;
    current[1] += index % 6 === 0 ? 1 : 0;
    return { date, ratings: { ...current } };
  });
}

const baseProducts: ProductReviewHistory[] = [
  {
    id: "p1",
    article: "165489021",
    name: "Блузка шелковая с принтом",
    snapshots: buildSnapshots({ 1: 13, 2: 24, 3: 51, 4: 112, 5: 390 }, [3, 5, 0, -2, 4, 1, 6]),
  },
  {
    id: "p2",
    article: "172901452",
    name: "Брюки классические Black",
    snapshots: buildSnapshots({ 1: 11, 2: 31, 3: 62, 4: 124, 5: 758 }, [12, 3, -1, 0, 5, 3, 4]),
  },
  {
    id: "p3",
    article: "198002341",
    name: "Платье летнее в горошек",
    snapshots: buildSnapshots({ 1: 8, 2: 14, 3: 29, 4: 68, 5: 186 }, [2, 0, 3, 6, 1, 0, 3]),
  },
  {
    id: "p4",
    article: "154782003",
    name: "Кардиган оверсайз Beige",
    snapshots: buildSnapshots({ 1: 4, 2: 6, 3: 12, 4: 24, 5: 44 }, [-2, 0, 2, 3, -2, 0, 1]),
  },
  {
    id: "p5",
    article: "204593872",
    name: "Жакет укороченный графит",
    snapshots: buildSnapshots({ 1: 6, 2: 9, 3: 21, 4: 54, 5: 276 }, [1, 4, 2, 0, 3, -1, 5]),
  },
];

export const histories = Object.fromEntries(
  sellers.map((seller, sellerIndex) => [
    seller.id,
    baseProducts.map((product, productIndex) => ({
      ...product,
      id: `${seller.id}-${product.id}`,
      article: String(Number(product.article) + sellerIndex * 1000 + productIndex),
      snapshots: product.snapshots.map((snapshot) => ({
        ...snapshot,
        ratings: Object.fromEntries(
          Object.entries(snapshot.ratings).map(([rating, count]) => [rating, count + sellerIndex * 17]),
        ) as unknown as RatingCounts,
      })),
    })),
  ]),
);
