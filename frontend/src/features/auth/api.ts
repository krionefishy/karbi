import { apiRequest } from "../../api/http";
import type { CurrentUser, TokenResponse } from "./types";

export function login(username: string, password: string) {
  return apiRequest<TokenResponse>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function getCurrentUser() {
  return apiRequest<CurrentUser>("/api/v1/auth/me");
}

export async function logout() {
  await apiRequest<{ status: string }>("/api/v1/auth/logout", { method: "POST" });
}
