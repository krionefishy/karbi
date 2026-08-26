import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";

import { ApiError } from "../api/http";
import { createWarehouse, getSetup } from "../features/fbs/api";
import type { FbsWarehouse } from "../features/fbs/types";

interface Props {
  sellerId: string;
  writeEnabled: boolean;
  warehouses: FbsWarehouse[];
}

export function FbsWarehouseAdmin({ sellerId, writeEnabled, warehouses }: Props) {
  const queryClient = useQueryClient();
  const { data: setup } = useQuery({ queryKey: ["fbs-setup"], queryFn: getSetup });
  const [officeId, setOfficeId] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  const taken = new Set(warehouses.map((warehouse) => warehouse.office_id));
  // Только объекты, у которых есть направление: без него склад в распределении
  // всё равно не участвует, и создавать его рано.
  const available = (setup?.offices ?? []).filter((office) => office.region_code && !taken.has(office.office_id));

  const create = useMutation({
    mutationFn: () => createWarehouse(sellerId, { office_id: Number(officeId), name }),
    onSuccess: async () => {
      setError("");
      setOfficeId("");
      setName("");
      await queryClient.invalidateQueries({ queryKey: ["fbs-overview", sellerId] });
      await queryClient.invalidateQueries({ queryKey: ["fbs-queue", sellerId] });
      await queryClient.invalidateQueries({ queryKey: ["fbs-setup"] });
    },
    onError: (raised) => setError(raised instanceof ApiError ? raised.message : "Не удалось создать склад"),
  });

  return (
    <>
      <div className="reviews-heading">
        <div>
          <h2>Создать склад</h2>
          <p className="muted">
            {writeEnabled
              ? "Создаёт виртуальный склад в живом кабинете Wildberries под выбранным объектом."
              : "Кабинету не разрешена запись в Wildberries — включите её на вкладке выше."}
            {" "}
            Объектов с направлением и без склада: {available.length}.
          </p>
        </div>
      </div>
      {error && <div className="form-error">{error}</div>}
      <section className="fbs-settings" aria-label="Создание склада">
        <label>
          Объект Wildberries
          <select
            aria-label="Объект для нового склада"
            value={officeId}
            onChange={(event) => {
              setOfficeId(event.target.value);
              const office = available.find((item) => String(item.office_id) === event.target.value);
              if (office && !name) setName(office.name);
            }}
          >
            <option value="">выберите объект</option>
            {available.map((office) => (
              <option key={office.office_id} value={office.office_id}>
                {office.city} — {office.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Название склада
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Как назвать в кабинете" />
        </label>
        <label>
          {" "}
          <button
            className="primary-button"
            disabled={!writeEnabled || !officeId || !name.trim() || create.isPending}
            onClick={() => create.mutate()}
          >
            <Plus size={16} />
            {create.isPending ? "Создаём…" : "Создать в Wildberries"}
          </button>
        </label>
      </section>
    </>
  );
}
