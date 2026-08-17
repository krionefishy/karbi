import { delay, http, HttpResponse } from "msw";

import { automations, histories, sellers } from "./data";

export const handlers = [
  http.post("/api/v1/auth/login", async ({ request }) => {
    const credentials = (await request.json()) as { username?: string; password?: string };
    await delay(250);
    if (!credentials.username || !credentials.password) {
      return HttpResponse.json({ detail: "Введите логин и пароль" }, { status: 401 });
    }
    return HttpResponse.json({
      access_token: "mock-access-token",
      token_type: "bearer",
      expires_in: 86_400,
      user: { id: "mock-user", username: credentials.username },
    });
  }),
  http.get("/api/v1/auth/me", () => HttpResponse.json({ id: "mock-user", username: "demo" })),
  http.post("/api/v1/auth/logout", () => HttpResponse.json({ status: "ok" })),
  http.get("/api/v1/automations", async () => {
    await delay(180);
    return HttpResponse.json(automations);
  }),
  http.get("/api/v1/wb-reviews/sellers", async () => {
    await delay(180);
    return HttpResponse.json(sellers);
  }),
  http.get("/api/v1/wb-reviews/sellers/:sellerId/history", async ({ params }) => {
    await delay(220);
    const products = histories[String(params.sellerId)];
    if (!products) return HttpResponse.json({ detail: "Селлер не найден" }, { status: 404 });
    return HttpResponse.json({ seller_id: params.sellerId, products });
  }),
];
