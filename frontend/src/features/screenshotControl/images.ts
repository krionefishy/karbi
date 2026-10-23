// Presigned-ссылки на картинки скриншотов. Сервер подписывает ссылку заново в каждом ответе, и если
// подставлять её в <img> как есть, браузер перекачивает миниатюру на каждом опросе. Поэтому первая
// ссылка по id держится, пока не истекла, а новая берётся только после этого или после ошибки загрузки.

const MARGIN_MS = 30_000;
/** Если срок из ссылки не прочитать: TTL на сервере 5 минут, берём с запасом. */
const DEFAULT_TTL_MS = 4 * 60_000;

interface Entry {
  url: string;
  expiresAt: number;
}

const cache = new Map<string, Entry>();

/** Срок presigned-ссылки S3: X-Amz-Date + X-Amz-Expires (SigV4) или Expires (SigV2). */
export function presignedExpiry(url: string, now: number): number {
  try {
    const params = new URL(url, "http://local").searchParams;
    const date = params.get("X-Amz-Date")?.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/);
    const expires = params.get("X-Amz-Expires");
    if (date && expires && /^\d+$/.test(expires)) {
      const [, y, mo, d, h, mi, s] = date.map(Number);
      return Date.UTC(y, mo - 1, d, h, mi, s) + Number(expires) * 1000;
    }
    const legacy = params.get("Expires");
    if (legacy && /^\d+$/.test(legacy)) return Number(legacy) * 1000;
  } catch {
    // не URL — живёт по умолчанию
  }
  return now + DEFAULT_TTL_MS;
}

/**
 * Ссылка для <img> по ключу картинки: прежняя, пока не истекла, иначе пришедшая сейчас.
 * null — картинки нет (очищена или ещё не выгружена), запомненная ссылка забывается.
 */
export function stableImageUrl(
  key: string,
  url: string | null | undefined,
  options: { expiresAt?: string | null; now?: number } = {},
): string | null {
  if (!url) {
    cache.delete(key);
    return null;
  }
  const now = options.now ?? Date.now();
  const cached = cache.get(key);
  if (cached && cached.expiresAt - MARGIN_MS > now) return cached.url;
  const explicit = options.expiresAt ? Date.parse(options.expiresAt) : Number.NaN;
  cache.set(key, { url, expiresAt: Number.isNaN(explicit) ? presignedExpiry(url, now) : explicit });
  return url;
}

/** Картинка не загрузилась (ссылка протухла): следующая ссылка из ответа сервера примется сразу. */
export function forgetImage(key: string): void {
  cache.delete(key);
}

export function clearImageCache(): void {
  cache.clear();
}

export const thumbKey = (submissionId: string) => `thumb:${submissionId}`;
export const originalKey = (submissionId: string) => `original:${submissionId}`;
