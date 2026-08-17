import { delay, http, HttpResponse } from "msw";

import { automations, histories, sellers } from "./data";

export const handlers = [
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
