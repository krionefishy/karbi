import { apiDownload, apiRequest } from "../../api/http";
import type { Podsort, PodsortSettings, PodsortSettingsInput } from "./types";

const root = "/api/v1/wb/podsort";

export const getPodsort = (region: string) =>
  apiRequest<Podsort>(region ? `${root}?${new URLSearchParams({ region })}` : root);

export const saveSettings = (payload: PodsortSettingsInput) =>
  apiRequest<PodsortSettings>(`${root}/settings`, { method: "PUT", body: JSON.stringify(payload) });

export const setWarehouseRegion = (name: string, region: string | null, guess = false) =>
  apiRequest<void>(`${root}/warehouses`, { method: "PUT", body: JSON.stringify({ name, region, guess }) });

export const downloadPodsort = () => apiDownload(`${root}/export`);
