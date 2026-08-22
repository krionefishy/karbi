import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, CircleCheck, KeyRound, Plus, Shield, ShieldOff } from "lucide-react";
import { useState } from "react";

import { ApiError } from "../../api/http";
import { AdminHeader } from "../../components/AdminHeader";
import { EmployeeDialog, IssuedPasswordDialog } from "../../components/EmployeeDialog";
import { createEmployee, getEmployees, resetEmployeePassword, updateEmployee } from "../../features/admin/api";
import type { Employee, IssuedPassword } from "../../features/admin/types";
import { useAuth } from "../../features/auth/AuthContext";

const dateFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" });

export function EmployeesPage() {
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const [creating, setCreating] = useState(false);
  const [issued, setIssued] = useState<IssuedPassword | null>(null);
  const [formError, setFormError] = useState("");
  const [rowError, setRowError] = useState("");

  const { data: employees = [], isLoading } = useQuery({ queryKey: ["employees"], queryFn: getEmployees });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["employees"] });

  const createMutation = useMutation({
    mutationFn: createEmployee,
    onSuccess: async (result) => {
      setCreating(false);
      setFormError("");
      setIssued(result);
      await refresh();
    },
    onError: (error) =>
      setFormError(error instanceof ApiError ? error.message : "Не удалось завести сотрудника"),
  });
  const updateMutation = useMutation({
    mutationFn: ({ id, ...payload }: { id: string; is_active?: boolean; is_admin?: boolean }) =>
      updateEmployee(id, payload),
    onSuccess: async () => {
      setRowError("");
      await refresh();
    },
    onError: (error) => setRowError(error instanceof ApiError ? error.message : "Не удалось применить изменение"),
  });
  const resetMutation = useMutation({
    mutationFn: resetEmployeePassword,
    onSuccess: (result) => {
      setRowError("");
      setIssued(result);
    },
    onError: (error) => setRowError(error instanceof ApiError ? error.message : "Не удалось сбросить пароль"),
  });

  return (
    <div className="app-page">
      <AdminHeader />
      <main className="page-container">
        <div className="page-heading">
          <div>
            <p className="eyebrow">Доступ</p>
            <h1>Сотрудники</h1>
            <p className="muted">
              Учётные записи для входа в интерфейс. Пароль генерируется системой и показывается один раз —
              забыли, значит выдаём новый.
            </p>
          </div>
          <div className="registry-actions">
            <button
              className="primary-button"
              onClick={() => {
                setFormError("");
                setCreating(true);
              }}
            >
              <Plus size={15} />
              Добавить сотрудника
            </button>
          </div>
        </div>

        {rowError && (
          <p className="form-error" role="alert">
            {rowError}
          </p>
        )}

        {isLoading ? (
          <div className="loading-block">Загружаем сотрудников…</div>
        ) : (
          <section className="registry-table">
            <div className="staff-head">
              <span>Сотрудник</span>
              <span>Роль</span>
              <span>Статус</span>
              <span>Последний вход</span>
              <span>Действия</span>
            </div>
            {employees.map((employee: Employee) => {
              const isSelf = employee.id === user?.id;
              return (
                <div className={`staff-row${employee.is_active ? "" : " registry-archived"}`} key={employee.id}>
                  <div className="registry-name">
                    <strong>{employee.username}</strong>
                    {isSelf && <em>это вы</em>}
                  </div>
                  <span>{employee.is_admin ? "Администратор" : "Сотрудник"}</span>
                  <span className={employee.is_active ? "staff-active" : "staff-blocked"}>
                    {employee.is_active ? "Активен" : "Заблокирован"}
                  </span>
                  <span>
                    {employee.last_login_at ? dateFormatter.format(new Date(employee.last_login_at)) : "—"}
                  </span>
                  <span className="registry-row-actions">
                    <button
                      title="Выдать новый пароль"
                      aria-label={`Выдать новый пароль ${employee.username}`}
                      onClick={() => resetMutation.mutate(employee.id)}
                    >
                      <KeyRound size={15} />
                    </button>
                    <button
                      title={employee.is_admin ? "Забрать админку" : "Выдать админку"}
                      aria-label={`${employee.is_admin ? "Забрать" : "Выдать"} админку ${employee.username}`}
                      disabled={isSelf && employee.is_admin}
                      onClick={() =>
                        updateMutation.mutate({ id: employee.id, is_admin: !employee.is_admin })
                      }
                    >
                      {employee.is_admin ? <ShieldOff size={15} /> : <Shield size={15} />}
                    </button>
                    <button
                      className={employee.is_active ? "danger-icon" : undefined}
                      title={employee.is_active ? "Заблокировать" : "Разблокировать"}
                      aria-label={`${employee.is_active ? "Заблокировать" : "Разблокировать"} ${employee.username}`}
                      disabled={isSelf && employee.is_active}
                      onClick={() =>
                        updateMutation.mutate({ id: employee.id, is_active: !employee.is_active })
                      }
                    >
                      {employee.is_active ? <Ban size={15} /> : <CircleCheck size={15} />}
                    </button>
                  </span>
                </div>
              );
            })}
          </section>
        )}
      </main>

      {creating && (
        <EmployeeDialog
          pending={createMutation.isPending}
          error={formError}
          onClose={() => setCreating(false)}
          onSubmit={(value) => createMutation.mutate(value)}
        />
      )}
      {issued && <IssuedPasswordDialog issued={issued} onClose={() => setIssued(null)} />}
    </div>
  );
}
