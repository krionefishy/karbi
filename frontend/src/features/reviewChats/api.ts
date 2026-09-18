import { apiDownload, apiRequest } from "../../api/http";
import type { ReviewChats, ReviewChatsFilter } from "./types";

const root = "/api/v1/wb/review-chats";
const base = (sellerId: string) => `${root}/sellers/${sellerId}`;

export const PAGE_SIZE = 100;

export function getReviewChats(sellerId: string, filter: ReviewChatsFilter) {
  const params = new URLSearchParams({
    date_from: filter.dateFrom,
    date_to: filter.dateTo,
    page: String(filter.page),
    page_size: String(PAGE_SIZE),
  });
  if (filter.group) params.set("group", filter.group);
  if (filter.outcome) params.set("outcome", filter.outcome);
  return apiRequest<ReviewChats>(`${base(sellerId)}?${params.toString()}`);
}

/** Книга за период целиком: сводка с днями и все диалоги, фильтры списка на неё не влияют. */
export const downloadReviewChats = (sellerId: string, filter: ReviewChatsFilter) =>
  apiDownload(`${base(sellerId)}/export?${new URLSearchParams({ date_from: filter.dateFrom, date_to: filter.dateTo })}`);
