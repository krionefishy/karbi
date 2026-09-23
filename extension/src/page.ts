/** Функции, которые выполняются в мире страницы wildberries.ru — от её имени, с её JWT и антибот-токеном.
 *
 * Здесь нельзя ссылаться ни на что снаружи: функция сериализуется и запускается через
 * chrome.scripting.executeScript({ world: "MAIN" }). Возвращаем сырые ответы; разбор — на сервере.
 */

export interface PageResult {
  loggedIn: boolean;
  codes: unknown;
  codesStatus: number;
  deliveries: unknown;
  deliveriesStatus: number;
  error: string | null;
}

export async function collectFromPage(): Promise<PageResult> {
  const result: PageResult = {
    loggedIn: false,
    codes: null,
    codesStatus: 0,
    deliveries: null,
    deliveriesStatus: 0,
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

  function tokenFromStorage(): string | null {
    for (const key of ["wbx__tokenData", "wbx__tokenDataStage"]) {
      const raw = localStorage.getItem(key);
      if (!raw) continue;
      try {
        const found = findJwt(JSON.parse(raw));
        if (found) return found;
      } catch {
        if (jwtLike.test(raw)) return raw;
      }
    }
    return null;
  }

  function cookie(name: string): string | null {
    const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
    return match ? decodeURIComponent(match[1]) : null;
  }

  try {
    const deliveries = await fetch("/webapi/lk/myorders/delivery/get", {
      method: "GET",
      credentials: "include",
      headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
      redirect: "follow",
    });
    result.deliveriesStatus = deliveries.status;
    const location = deliveries.url || "";
    if (deliveries.status === 401 || deliveries.status === 403 || /login|auth/i.test(location)) {
      result.loggedIn = false;
    } else {
      result.loggedIn = deliveries.ok;
      try {
        result.deliveries = await deliveries.json();
      } catch {
        result.deliveries = null;
      }
    }
  } catch (error) {
    result.error = `deliveries: ${String(error)}`;
  }

  const jwt = tokenFromStorage();
  if (!jwt) {
    result.loggedIn = false;
    result.error = result.error ?? "no jwt in localStorage";
    return result;
  }

  try {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${jwt}`,
      "WB-AppType": "site",
      "WB-AppVersion": "700",
      Accept: "application/json",
    };
    const antibot = cookie("x_wbaas_token");
    if (antibot) headers["x-wbaas-token"] = antibot;
    const codes = await fetch("https://delivery-code.wildberries.ru/delivery-code/api/v1/otp/get", {
      method: "GET",
      credentials: "include",
      headers,
    });
    result.codesStatus = codes.status;
    if (codes.status === 401) result.loggedIn = false;
    try {
      result.codes = await codes.json();
    } catch {
      result.codes = null;
    }
    if (codes.ok) result.loggedIn = true;
  } catch (error) {
    result.error = `${result.error ? result.error + "; " : ""}codes: ${String(error)}`;
  }
  return result;
}
