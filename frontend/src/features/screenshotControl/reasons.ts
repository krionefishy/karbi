// Причины вердикта для админа: русская фраза на каждый код из закрытого словаря ReasonCode.
// Менеджер этих фраз не видит — у него свой узкий словарь и готовый reason_text с сервера.

import type { Reason, ReasonCode, Severity } from "./types";

export const REASON_TEXT: Record<ReasonCode, string> = {
  exact_file: "тот же файл уже присылали",
  case_key: "тот же кейс: совпали чат и время отзыва",
  near_image: "картинка почти совпадает с уже присланной",
  near_image_conflict: "картинка похожа, но поля отличаются",
  cross_user_duplicate: "тот же отзыв уже прислал другой менеджер",
  caption_conflict: "у совпавших скринов разные подписи",
  same_chat_same_text_other_time: "тот же чат и текст, время другое",
  same_chat_similar_text: "тот же чат, похожий текст",
  same_text_time_other_chat: "то же время и текст, чат другой",
  same_text_other_time: "тот же текст, время другое",
  messages_overlap: "совпадают сообщения покупателя",
  possible_duplicate: "возможный дубль",
  order_conflict: "расходится с заказом",
  weak_key: "слабый ключ, короткий текст",
  extraction_disagreement_chat_id: "VLM и OCR разошлись по чату",
  extraction_disagreement_time: "VLM и OCR разошлись по времени",
  extraction_disagreement_text: "VLM и OCR разошлись по тексту",
  extraction_failed: "распознать не получилось",
  chat_id_unreadable: "не читается номер чата",
  review_time_unreadable: "не читается время отзыва",
  review_date_implausible: "дата отзыва слишком старая или в будущем",
  url_not_chat: "адрес страницы — не чат WB",
  not_wb_screenshot: "не скриншот чата WB",
  unreadable: "скриншот не читается",
  cropped_missing_panel: "обрезана панель с отзывом",
  photo_of_screen: "фото экрана",
  possible_tampering: "похоже на правку картинки",
  late_upload: "загружен с большой задержкой",
  clock_skew: "часы устройства расходятся с сервером",
  upload_burst: "всплеск загрузок за час",
  no_outcome: "нет подписи",
  audit_sample: "случайная проверка",
};

/** Группы для фильтра очереди — в порядке словаря на бэкенде. */
export const REASON_GROUPS: { label: string; codes: ReasonCode[] }[] = [
  {
    label: "Совпадения",
    codes: [
      "exact_file",
      "case_key",
      "near_image",
      "near_image_conflict",
      "cross_user_duplicate",
      "caption_conflict",
      "same_chat_same_text_other_time",
      "same_chat_similar_text",
      "same_text_time_other_chat",
      "same_text_other_time",
      "messages_overlap",
      "possible_duplicate",
      "order_conflict",
      "weak_key",
    ],
  },
  {
    label: "Распознавание",
    codes: [
      "extraction_disagreement_chat_id",
      "extraction_disagreement_time",
      "extraction_disagreement_text",
      "extraction_failed",
      "chat_id_unreadable",
      "review_time_unreadable",
      "review_date_implausible",
      "url_not_chat",
      "not_wb_screenshot",
      "unreadable",
      "cropped_missing_panel",
    ],
  },
  { label: "Подлинность и поведение", codes: ["photo_of_screen", "possible_tampering", "late_upload", "clock_skew", "upload_burst"] },
  { label: "Прочее", codes: ["no_outcome", "audit_sample"] },
];

export const reasonText = (code: ReasonCode): string => REASON_TEXT[code] ?? code;

const SEVERITY_RANK: Record<Severity, number> = { high: 0, warn: 1, info: 2 };

/** Главная причина: самая серьёзная, при равенстве — первая из присланных. */
export function primaryReason(reasons: Reason[]): Reason | null {
  let best: Reason | null = null;
  for (const reason of reasons) {
    if (!best || SEVERITY_RANK[reason.severity] < SEVERITY_RANK[best.severity]) best = reason;
  }
  return best;
}

/** Причины одной строкой, главная первой. */
export function reasonsLine(reasons: Reason[]): string {
  const first = primaryReason(reasons);
  if (!first) return "без причины";
  const rest = reasons.filter((reason) => reason !== first).map((reason) => reasonText(reason.code));
  return [reasonText(first.code), ...rest].join("; ");
}
