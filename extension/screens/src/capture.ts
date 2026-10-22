/** Захват видимой вкладки, проверка адреса и ужатие больших снимков. */

export const DEFAULT_ALLOWED_PREFIXES = ["https://seller.wildberries.ru/chat-with-clients"] as const;
export const MAX_UPLOAD_BYTES = 6 * 1024 * 1024;
export const JPEG_QUALITY = 0.92;

/**
 * Адрес вкладки начинается с одного из разрешённых префиксов, и префикс кончается на границе
 * сегмента: `/chat-with-clients?chatId=…` подходит, `/chat-with-clients-old` и чужой хост — нет.
 */
export function isAllowedUrl(url: string | undefined | null, prefixes: readonly string[]): boolean {
  if (!url) return false;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password) return false;
  const actual = `${parsed.origin}${parsed.pathname}${parsed.search}${parsed.hash}`;
  return prefixes.some((raw) => {
    let prefix: string;
    try {
      const base = new URL(raw);
      prefix = `${base.origin}${base.pathname === "/" && !raw.endsWith("/") ? "" : base.pathname}`;
    } catch {
      return false;
    }
    if (!actual.startsWith(prefix)) return false;
    if (prefix.endsWith("/")) return true;
    const next = actual.charAt(prefix.length);
    return next === "" || next === "/" || next === "?" || next === "#";
  });
}

export interface Capture {
  id: string;
  blob: Blob;
  dataUrl: string;
  url: string;
  capturedAt: string;
  tzOffsetMin: number;
  viewportW: number | null;
  viewportH: number | null;
  dpr: number | null;
}

/** Минимум chrome.tabs, нужный для захвата: подменяется в тестах. */
export interface TabsApi {
  query(info: chrome.tabs.QueryInfo): Promise<chrome.tabs.Tab[]>;
  captureVisibleTab(windowId: number, options: chrome.tabs.CaptureVisibleTabOptions): Promise<string>;
}

export class CaptureError extends Error {}

export function dataUrlToBlob(dataUrl: string): Blob {
  const match = /^data:([^;,]+)(;base64)?,(.*)$/s.exec(dataUrl);
  if (!match) throw new CaptureError("Снимок вкладки не получился");
  const [, mime, base64, payload] = match;
  if (!base64) return new Blob([decodeURIComponent(payload)], { type: mime });
  const binary = atob(payload);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

/**
 * Активная вкладка и её снимок. Работает по правам `activeTab`: попап открыт кликом по иконке
 * или Alt+Shift+S, это жест пользователя — Chrome даёт url и captureVisibleTab только этой вкладке.
 */
export async function captureActiveTab(tabs: TabsApi, now: () => Date = () => new Date(), fallbackDpr = 1): Promise<Capture> {
  const [tab] = await tabs.query({ active: true, currentWindow: true });
  if (!tab || tab.windowId === undefined) throw new CaptureError("Не нашли активную вкладку");
  const capturedAt = now();
  let dataUrl: string;
  try {
    dataUrl = await tabs.captureVisibleTab(tab.windowId, { format: "png" });
  } catch (error) {
    throw new CaptureError(`Браузер не дал снять вкладку: ${error instanceof Error ? error.message : String(error)}`);
  }
  const blob = dataUrlToBlob(dataUrl);
  const viewportW = tab.width ?? null;
  const viewportH = tab.height ?? null;
  return {
    id: crypto.randomUUID(),
    blob,
    dataUrl,
    url: tab.url ?? tab.pendingUrl ?? "",
    capturedAt: capturedAt.toISOString(),
    // Как Date.getTimezoneOffset: минуты до UTC, для Москвы −180. Сервер этому не верит, это метаданные.
    tzOffsetMin: capturedAt.getTimezoneOffset(),
    viewportW,
    viewportH,
    dpr: fallbackDpr > 0 ? Math.round(fallbackDpr * 100) / 100 : null,
  };
}

export interface Encoder {
  /** Декодирует картинку и кодирует её в JPEG с заданным качеством. */
  toJpeg(blob: Blob, quality: number): Promise<Blob>;
}

export const offscreenEncoder: Encoder = {
  async toJpeg(blob, quality) {
    const bitmap = await createImageBitmap(blob);
    try {
      const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
      const context = canvas.getContext("2d");
      if (!context) throw new CaptureError("Не удалось перекодировать снимок");
      // JPEG без альфы: подложка белая, как фон кабинета.
      context.fillStyle = "#ffffff";
      context.fillRect(0, 0, bitmap.width, bitmap.height);
      context.drawImage(bitmap, 0, 0);
      return await canvas.convertToBlob({ type: "image/jpeg", quality });
    } finally {
      bitmap.close();
    }
  },
};

/** PNG больше 6 МБ перекодируется в JPEG q=0.92; меньший отдаётся как есть. */
export async function fitForUpload(blob: Blob, encoder: Encoder = offscreenEncoder, limit = MAX_UPLOAD_BYTES): Promise<Blob> {
  if (blob.size <= limit) return blob;
  return encoder.toJpeg(blob, JPEG_QUALITY);
}

export function fileNameFor(blob: Blob): string {
  return blob.type === "image/jpeg" ? "tab.jpg" : "tab.png";
}
