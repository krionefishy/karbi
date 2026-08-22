import { apiRequest } from "../../api/http";
import type { BotCreate, Employee, EmployeeUpdate, IssuedPassword, NotificationBot } from "./types";

export const getEmployees = () => apiRequest<Employee[]>("/api/v1/admin/users");

export const createEmployee = (payload: { username: string; is_admin: boolean }) =>
  apiRequest<IssuedPassword>("/api/v1/admin/users", { method: "POST", body: JSON.stringify(payload) });

export const updateEmployee = (userId: string, payload: EmployeeUpdate) =>
  apiRequest<Employee>(`/api/v1/admin/users/${userId}`, { method: "PATCH", body: JSON.stringify(payload) });

/** Issues a new password; the previous one stops working immediately. */
export const resetEmployeePassword = (userId: string) =>
  apiRequest<IssuedPassword>(`/api/v1/admin/users/${userId}/password`, { method: "POST" });

export const getBots = () => apiRequest<NotificationBot[]>("/api/v1/admin/bots");

/** The token travels to the relay and is never stored on our side. */
export const createBot = (payload: BotCreate) =>
  apiRequest<NotificationBot>("/api/v1/admin/bots", { method: "POST", body: JSON.stringify(payload) });

export const deleteBot = (code: string) =>
  apiRequest<void>(`/api/v1/admin/bots/${encodeURIComponent(code)}`, { method: "DELETE" });
