import { apiRequest, refreshAccessToken } from "../../api/http";
import type { TokenResponse } from "./types";

export function login(username: string, password: string) {
  return apiRequest<TokenResponse>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  }, { skipRefresh: true });
}

export function restoreSession() {
  return refreshAccessToken() as Promise<TokenResponse>;
}

export async function logout() {
  await apiRequest<{ status: string }>("/api/v1/auth/logout", { method: "POST" });
}
