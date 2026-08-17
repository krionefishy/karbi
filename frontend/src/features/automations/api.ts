import { apiRequest } from "../../api/http";
import type { Automation } from "./types";

export function getAutomations() {
  return apiRequest<Automation[]>("/api/v1/automations");
}
