import { apiDownload, apiRequest } from "../../api/http";
import type { BoxStickerPlan, PackingPlan, RecentPacking, WorkbookSource } from "./types";

const root = "/api/v1/wb/box-stickers";

function form(workbook: WorkbookSource, stickers: File): FormData {
  const body = new FormData();
  if (workbook.kind === "file") {
    body.append("workbook", workbook.file);
  } else {
    body.append("supply_id", String(workbook.supplyId));
  }
  body.append("stickers", stickers);
  return body;
}

/** Сопоставление без сборки: какой стикер на каком месте и что мешает. */
export const checkStickers = (workbook: WorkbookSource, stickers: File) =>
  apiRequest<BoxStickerPlan>(`${root}/check`, { method: "POST", body: form(workbook, stickers) });

/** PDF в порядке строк Excel: страницы WB как есть, без допечаток. */
export const buildStickers = (workbook: WorkbookSource, stickers: File) =>
  apiDownload(`${root}/build`, {
    method: "POST",
    body: form(workbook, stickers),
  });

function packingForm(template: File, assembly: string): FormData {
  const body = new FormData();
  body.append("template", template);
  body.append("assembly", assembly);
  return body;
}

/** Раскладка строк сборки по коробам шаблона — без файла, с проблемами. */
export const checkPacking = (template: File, assembly: string) =>
  apiRequest<PackingPlan>(`${root}/packing/check`, { method: "POST", body: packingForm(template, assembly) });

/** Заполненный шаблон WB; он же запоминается под номером поставки. */
export const buildPacking = (template: File, assembly: string) =>
  apiDownload(`${root}/packing/build`, { method: "POST", body: packingForm(template, assembly) });

export const fetchRecentPackings = () => apiRequest<RecentPacking[]>(`${root}/packing/recent`);
