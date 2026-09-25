import { apiDownload, apiRequest } from "../../api/http";
import type { BoxStickerPlan } from "./types";

const root = "/api/v1/wb/box-stickers";

function form(workbook: File, stickers: File): FormData {
  const body = new FormData();
  body.append("workbook", workbook);
  body.append("stickers", stickers);
  return body;
}

/** Сопоставление без сборки: какой стикер на каком месте и что мешает. */
export const checkStickers = (workbook: File, stickers: File) =>
  apiRequest<BoxStickerPlan>(`${root}/check`, { method: "POST", body: form(workbook, stickers) });

/** PDF в порядке строк Excel: страницы WB как есть, без допечаток. */
export const buildStickers = (workbook: File, stickers: File) =>
  apiDownload(`${root}/build`, {
    method: "POST",
    body: form(workbook, stickers),
  });
