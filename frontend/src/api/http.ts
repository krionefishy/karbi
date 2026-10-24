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
  user: { id: string; username: string; is_admin: boolean; services: string[] };
}

interface RequestOptions {
  skipRefresh?: boolean;
}

export function setAccessToken(token: string | null) {
  accessToken = token;
}

// Сервер отвечает 403 с этим заголовком, когда доступ к сервису сняли, а страница об этом ещё не знает.
const ACCESS_DENIED_HEADER = "X-Access-Denied";
let accessDeniedHandler: (() => void) | null = null;

export function setAccessDeniedHandler(handler: (() => void) | null) {
  accessDeniedHandler = handler;
}

async function failure(response: Response): Promise<ApiError> {
  if (response.status === 403 && response.headers.get(ACCESS_DENIED_HEADER)) accessDeniedHandler?.();
  const body = (await response.json().catch(() => null)) as { detail?: string } | null;
  return new ApiError(response.status, body?.detail ?? "Ошибка запроса");
}

async function send(path: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  if (init?.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
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

export async function apiDownload(path: string, init?: RequestInit): Promise<FileDownload> {
  let response = await send(path, init);
  if (response.status === 401) {
    try {
      await refreshAccessToken();
      response = await send(path, init);
    } catch (error) {
      if (isSessionGone(error)) setAccessToken(null);
      throw error;
    }
  }
  if (!response.ok) throw await failure(response);
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
  if (!response.ok) throw await failure(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
