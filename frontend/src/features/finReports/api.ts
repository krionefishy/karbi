import { apiDownload, apiRequest } from "../../api/http";
import type { CostUploadResult, Granularity, Pnl } from "./types";

const root = "/api/v1/fin-reports";

export function getPnl(year: number, granularity: Granularity, sellerId: string) {
  const params = new URLSearchParams({ year: String(year), granularity });
  if (sellerId) params.set("seller_id", sellerId);
  return apiRequest<Pnl>(`${root}?${params.toString()}`);
}

/** Книга за год: выбранный период по кабинетам, недели, месяцы и артикулы без себестоимости. */
export function downloadPnl(year: number, period: string) {
  const params = new URLSearchParams({ year: String(year) });
  if (period) params.set("period", period);
  return apiDownload(`${root}/export?${params.toString()}`);
}

/** Файл себестоимости: цены действуют с указанной даты, без неё — с сегодняшнего дня. */
export function uploadCosts(workbook: File, effectiveFrom: string) {
  const body = new FormData();
  body.append("workbook", workbook);
  // Дата в прошлом — для исправления ошибки; без неё цены действуют с сегодняшнего дня.
  if (effectiveFrom) body.append("effective_from", effectiveFrom);
  return apiRequest<CostUploadResult>(`${root}/costs`, { method: "POST", body });
}
