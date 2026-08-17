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
  user: { id: string; username: string };
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
  if (init?.body) {
    headers.set("Content-Type", "application/json");
  }
  if (accessToken) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }
  return fetch(path, { ...init, headers, credentials: "include" });
}

export function refreshAccessToken(): Promise<AuthRefreshPayload> {
  if (!refreshPromise) {
    refreshPromise = fetch("/api/v1/auth/refresh", {
      method: "POST",
      credentials: "include",
      headers: { Accept: "application/json" },
    })
      .then(async (response) => {
        if (!response.ok) throw new ApiError(response.status, "Сессия истекла");
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

export async function apiRequest<T>(path: string, init?: RequestInit, options?: RequestOptions): Promise<T> {
  let response = await send(path, init);
  if (response.status === 401 && !options?.skipRefresh) {
    try {
      await refreshAccessToken();
      response = await send(path, init);
    } catch {
      setAccessToken(null);
    }
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new ApiError(response.status, body?.detail ?? "Ошибка запроса");
  }
  return (await response.json()) as T;
}
