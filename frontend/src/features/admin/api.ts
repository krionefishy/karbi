import { apiRequest } from "../../api/http";
import type { Employee, EmployeeUpdate, IssuedPassword } from "./types";

export const getEmployees = () => apiRequest<Employee[]>("/api/v1/admin/users");

export const createEmployee = (payload: { username: string; is_admin: boolean }) =>
  apiRequest<IssuedPassword>("/api/v1/admin/users", { method: "POST", body: JSON.stringify(payload) });

export const updateEmployee = (userId: string, payload: EmployeeUpdate) =>
  apiRequest<Employee>(`/api/v1/admin/users/${userId}`, { method: "PATCH", body: JSON.stringify(payload) });

/** Issues a new password; the previous one stops working immediately. */
export const resetEmployeePassword = (userId: string) =>
  apiRequest<IssuedPassword>(`/api/v1/admin/users/${userId}/password`, { method: "POST" });
