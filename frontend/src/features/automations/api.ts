import { apiRequest } from "../../api/http";
import type { Automation } from "./types";

export function getAutomations() {
  return apiRequest<Automation[]>("/api/v1/automations");
}

export function getPlatformReadiness() {
  return apiRequest<PlatformReadiness>("/api/v1/health/ready", undefined, { skipRefresh: true });
}

export interface PlatformReadiness {
  database: boolean;
  redis: boolean;
}
