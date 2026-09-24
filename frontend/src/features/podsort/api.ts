import { apiDownload, apiRequest } from "../../api/http";
import type { Podsort, PodsortSettings, PodsortSettingsInput } from "./types";

const root = "/api/v1/wb/podsort";

export const getPodsort = (region: string) =>
  apiRequest<Podsort>(region ? `${root}?${new URLSearchParams({ region })}` : root);

export const saveSettings = (payload: PodsortSettingsInput) =>
  apiRequest<PodsortSettings>(`${root}/settings`, { method: "PUT", body: JSON.stringify(payload) });

/** `region: null` — склад ни к какому региону не относится; `guess` — вернуть угадывание по городу. */
export const setWarehouseRegion = (name: string, region: string | null, guess = false) =>
  apiRequest<void>(`${root}/warehouses`, { method: "PUT", body: JSON.stringify({ name, region, guess }) });

/** Общая книга на все подключённые кабинеты: сводный лист по регионам и лист на кабинет. */
export const downloadPodsort = () => apiDownload(`${root}/export`);
