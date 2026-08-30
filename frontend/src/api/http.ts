export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

let accessToken: string | null = null;
let refreshPromise: Promise<AuthRefreshPayload> | null = null;

interface AuthRefreshPayload {
  access_token: string;
  user: { id: string; username: string; is_admin: boolean };
}

interface RequestOptions {
  skipRefresh?: boolean;
}

export function setAccessToken(token: string | null) {
  accessToken = token;
}

async function send(path: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  // JSON по умолчанию, но не поверх явно заданного: выгрузка 1С уезжает
  // текстом, и подменять ей тип нельзя.
  if (init?.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (accessToken) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }
  return fetch(path, { ...init, headers, credentials: "include" });
}

function isSessionGone(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

export function refreshAccessToken(): Promise<AuthRefreshPayload> {
  if (!refreshPromise) {
    refreshPromise = fetch("/api/v1/auth/refresh", {
      method: "POST",
      credentials: "include",
      headers: { Accept: "application/json" },
    })
      .then(async (response) => {
        // Сессию хоронит только отказ в правах. Лимитер (429), сетевой сбой или
        // 5xx — это «сейчас не получилось»: разлогинивать из-за них значит
        // выбрасывать человека из интерфейса каждый раз, когда сервер занят.
        if (response.status === 401 || response.status === 403) {
          throw new ApiError(response.status, "Сессия истекла");
        }
        if (!response.ok) throw new ApiError(response.status, "Не удалось обновить сессию");
        const payload = (await response.json()) as AuthRefreshPayload;
        setAccessToken(payload.access_token);
        return payload;
      })
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

export interface FileDownload {
  blob: Blob;
  filename: string | null;
}

function filenameFromDisposition(header: string | null): string | null {
  if (!header) return null;
  const utf8 = header.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8) {
    try {
      return decodeURIComponent(utf8[1]);
    } catch {
      // Падать из-за битого заголовка не стоит — ниже есть обычный filename.
    }
  }
  return header.match(/filename="?([^";]+)"?/i)?.[1] ?? null;
}

export async function apiDownload(path: string): Promise<FileDownload> {
  let response = await send(path);
  if (response.status === 401) {
    try {
      await refreshAccessToken();
      response = await send(path);
    } catch (error) {
      if (isSessionGone(error)) setAccessToken(null);
      throw error;
    }
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new ApiError(response.status, body?.detail ?? "Ошибка запроса");
  }
  return {
    blob: await response.blob(),
    filename: filenameFromDisposition(response.headers.get("Content-Disposition")),
  };
}

export async function apiRequest<T>(path: string, init?: RequestInit, options?: RequestOptions): Promise<T> {
  let response = await send(path, init);
  if (response.status === 401 && !options?.skipRefresh) {
    try {
      await refreshAccessToken();
      response = await send(path, init);
    } catch (error) {
      if (isSessionGone(error)) setAccessToken(null);
      throw error;
    }
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new ApiError(response.status, body?.detail ?? "Ошибка запроса");
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
