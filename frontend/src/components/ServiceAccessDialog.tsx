import { X } from "lucide-react";
import { type FormEvent, useState } from "react";

import type { Employee, ServiceInfo } from "../features/admin/types";
import { automationIcon } from "../features/automations/nav";

interface Props {
  employee: Employee;
  catalog: ServiceInfo[];
  pending: boolean;
  error: string;
  onClose: () => void;
  onSubmit: (serviceIds: string[]) => void;
}

export function ServiceAccessDialog({ employee, catalog, pending, error, onClose, onSubmit }: Props) {
  const [selected, setSelected] = useState(() => new Set(employee.services));

  function toggle(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit(catalog.filter((service) => selected.has(service.id)).map((service) => service.id));
  }

  return (
    <div className="modal-overlay" role="presentation">
      <section className="seller-dialog" role="dialog" aria-modal="true" aria-labelledby="service-access-title">
        <button className="dialog-close" onClick={onClose} aria-label="Закрыть">
          <X size={18} />
        </button>
        <h2 id="service-access-title">Сервисы: {employee.username}</h2>
        <p className="muted">
          {employee.is_admin
            ? "Администратор видит все сервисы, менять набор не нужно."
            : "Сотрудник увидит в меню только отмеченные сервисы. Изменения действуют сразу."}
        </p>
        <form className="seller-form" onSubmit={submit}>
          {!employee.is_admin && (
            <div className="access-bulk">
              <button
                type="button"
                className="link-button"
                onClick={() => setSelected(new Set(catalog.map((service) => service.id)))}
              >
                Выбрать все
              </button>
              <button type="button" className="link-button" onClick={() => setSelected(new Set())}>
                Снять все
              </button>
            </div>
          )}
          <div className="access-list">
            {catalog.map((service) => {
              const Icon = automationIcon(service.id);
              return (
                <label className="checkbox-field" key={service.id}>
                  <input
                    type="checkbox"
                    checked={employee.is_admin || selected.has(service.id)}
                    disabled={employee.is_admin}
                    onChange={() => toggle(service.id)}
                  />
                  <Icon size={16} aria-hidden="true" />
                  <span>{service.title}</span>
                </label>
              );
            })}
          </div>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <div className="dialog-actions">
            <button type="button" className="secondary-button" onClick={onClose}>
              {employee.is_admin ? "Закрыть" : "Отмена"}
            </button>
            {!employee.is_admin && (
              <button className="primary-button" disabled={pending}>
                {pending ? "Сохраняем…" : "Сохранить"}
              </button>
            )}
          </div>
        </form>
      </section>
    </div>
  );
}
