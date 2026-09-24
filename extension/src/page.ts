/** Функция, которая выполняется в мире страницы wildberries.ru — от её имени.
 *
 * Здесь нельзя ссылаться ни на что снаружи: функция сериализуется и запускается через
 * chrome.scripting.executeScript({ world: "MAIN" }). Со страницы берём только то, чего
 * нет у расширения: JWT сессии из localStorage, антибот-cookie и список доставок.
 * За кодом расширение ходит само — из сервис-воркера CORS не мешает.
 */

export interface PageResult {
  jwt: string | null;
  jwtSource: string;
  antibot: string | null;
  deliveries: unknown;
  deliveriesStatus: number;
  deliveriesUrl: string;
  error: string | null;
}

export async function collectFromPage(): Promise<PageResult> {
  const result: PageResult = {
    jwt: null,
    jwtSource: "",
    antibot: null,
    deliveries: null,
    deliveriesStatus: 0,
    deliveriesUrl: "",
    error: null,
  };
  const jwtLike = /^[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}$/;

  function findJwt(value: unknown, depth = 0): string | null {
    if (depth > 6 || value == null) return null;
    if (typeof value === "string") return jwtLike.test(value) ? value : null;
    if (Array.isArray(value)) {
      for (const item of value) {
        const found = findJwt(item, depth + 1);
        if (found) return found;
      }
      return null;
    }
    if (typeof value === "object") {
      for (const item of Object.values(value as Record<string, unknown>)) {
        const found = findJwt(item, depth + 1);
        if (found) return found;
      }
    }
    return null;
  }

  // Ключ с токеном сайт может переименовать: сначала известные, потом любой ключ с «token».
  const keys = ["wbx__tokenData", "wbx__tokenDataStage"];
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index);
    if (key && /token/i.test(key) && !keys.includes(key)) keys.push(key);
  }
  for (const key of keys) {
    const raw = localStorage.getItem(key);
    if (!raw) continue;
    let found: string | null = null;
    try {
      found = findJwt(JSON.parse(raw));
    } catch {
      found = jwtLike.test(raw) ? raw : null;
    }
    if (found) {
      result.jwt = found;
      result.jwtSource = key;
      break;
    }
  }

  const antibot = document.cookie.match(/(?:^|; )x_wbaas_token=([^;]*)/);
  result.antibot = antibot ? decodeURIComponent(antibot[1]) : null;

  try {
    const deliveries = await fetch("/webapi/lk/myorders/delivery/get", {
      method: "GET",
      credentials: "include",
      headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
      redirect: "follow",
    });
    result.deliveriesStatus = deliveries.status;
    result.deliveriesUrl = deliveries.url || "";
    if (deliveries.ok) {
      try {
        result.deliveries = await deliveries.json();
      } catch {
        result.deliveries = null;
      }
    }
  } catch (error) {
    result.error = `deliveries: ${String(error)}`;
  }
  return result;
}
