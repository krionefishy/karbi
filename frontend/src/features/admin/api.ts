import { apiRequest } from "../../api/http";
import type { BotCreate, Employee, EmployeeUpdate, IssuedPassword, NotificationBot, ServiceInfo } from "./types";

export const getEmployees = () => apiRequest<Employee[]>("/api/v1/admin/users");

export const createEmployee = (payload: { username: string; is_admin: boolean }) =>
  apiRequest<IssuedPassword>("/api/v1/admin/users", { method: "POST", body: JSON.stringify(payload) });

export const updateEmployee = (userId: string, payload: EmployeeUpdate) =>
  apiRequest<Employee>(`/api/v1/admin/users/${userId}`, { method: "PATCH", body: JSON.stringify(payload) });

export const getServices = () => apiRequest<ServiceInfo[]>("/api/v1/admin/users/services");

export const setEmployeeServices = (userId: string, serviceIds: string[]) =>
  apiRequest<Employee>(`/api/v1/admin/users/${userId}/services`, {
    method: "PUT",
    body: JSON.stringify({ service_ids: serviceIds }),
  });

export const resetEmployeePassword = (userId: string) =>
  apiRequest<IssuedPassword>(`/api/v1/admin/users/${userId}/password`, { method: "POST" });

export const getBots = () => apiRequest<NotificationBot[]>("/api/v1/admin/bots");

export const createBot = (payload: BotCreate) =>
  apiRequest<NotificationBot>("/api/v1/admin/bots", { method: "POST", body: JSON.stringify(payload) });

export const deleteBot = (code: string) =>
  apiRequest<void>(`/api/v1/admin/bots/${encodeURIComponent(code)}`, { method: "DELETE" });
