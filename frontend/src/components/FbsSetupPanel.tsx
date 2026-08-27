import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useEffect, useState } from "react";

import { ApiError } from "../api/http";
import { assignOffice, getSetup, saveRegions, saveSettings } from "../features/fbs/api";
import { cargoLabel } from "../features/fbs/cargo";
import { fromPercent, sharesAcceptable, sharesTotal, toPercent } from "../features/fbs/shares";
import type { FbsRegion } from "../features/fbs/types";

type OfficeFilter = "all" | "unassigned" | "used";

export function FbsSetupPanel() {
  const queryClient = useQueryClient();
  const { data: setup, isLoading } = useQuery({ queryKey: ["fbs-setup"], queryFn: getSetup });
  const [regions, setRegions] = useState<FbsRegion[]>([]);
  const [reserve, setReserve] = useState(20);
  const [priority, setPriority] = useState(3);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<OfficeFilter>("all");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!setup) return;
    setRegions(setup.regions);
    setReserve(setup.reserve_units);
    setPriority(setup.priority_regions);
  }, [setup]);

  const refresh = (next: unknown) => {
    queryClient.setQueryData(["fbs-setup"], next);
    setError("");
  };
  const fail = (fallback: string) => (raised: unknown) =>
    setError(raised instanceof ApiError ? raised.message : fallback);

  const regionsMutation = useMutation({
    mutationFn: () => saveRegions(regions.map((region) => ({ code: region.code, share_bp: region.share_bp }))),
    onSuccess: refresh,
    onError: fail("Не удалось сохранить направления"),
  });
  const settingsMutation = useMutation({
    mutationFn: () => saveSettings({ reserve_units: reserve, priority_regions: priority }),
    onSuccess: refresh,
    onError: fail("Не удалось сохранить настройки"),
  });
  const officeMutation = useMutation({
    mutationFn: ({ officeId, regionCode }: { officeId: number; regionCode: string | null }) =>
      assignOffice(officeId, regionCode),
    onSuccess: refresh,
    onError: fail("Не удалось разметить объект"),
  });

  function move(index: number, delta: number) {
    const next = [...regions];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    setRegions(next.map((region, position) => ({ ...region, position })));
  }

  function setShare(code: string, percent: number) {
    setRegions(regions.map((region) => (region.code === code ? { ...region, share_bp: fromPercent(percent) } : region)));
  }

  if (isLoading || !setup) return <div className="loading-block">Загружаем схему распределения…</div>;

  const total = sharesTotal(regions);
  const acceptable = sharesAcceptable(regions);
  const offices = setup.offices.filter((office) => {
    if (filter === "unassigned" && office.region_code) return false;
    if (filter === "used" && office.used_by_cabinets === 0) return false;
    const needle = search.toLowerCase();
    return (
      office.city.toLowerCase().includes(needle) ||
      office.name.toLowerCase().includes(needle) ||
      String(office.office_id).includes(needle)
    );
  });

  return (
    <>
      {error && <div className="form-error">{error}</div>}

      <div className="reviews-heading">
        <div>
          <h2>Направления</h2>
        </div>
        <button
          className="primary-button"
          disabled={!acceptable || regionsMutation.isPending}
          onClick={() => regionsMutation.mutate()}
        >
          {regionsMutation.isPending ? "Сохраняем…" : "Сохранить направления"}
        </button>
      </div>
      <section className="fbs-regions" aria-label="Логистические направления">
        {regions.map((region, index) => (
          <div className="fbs-region-row" key={region.code}>
            <span className="fbs-region-place">{index + 1}</span>
            <strong>{region.title}</strong>
            <label>
              <input
                aria-label={`Доля ${region.title}, процентов`}
                type="number"
                min={0}
                max={100}
                step={0.01}
                value={toPercent(region.share_bp)}
                onChange={(event) => setShare(region.code, Number(event.target.value))}
              />
              %
            </label>
            <span className="fbs-region-move">
              <button aria-label={`Поднять ${region.title}`} onClick={() => move(index, -1)} disabled={index === 0}>
                <ChevronUp size={14} />
              </button>
              <button
                aria-label={`Опустить ${region.title}`}
                onClick={() => move(index, 1)}
                disabled={index === regions.length - 1}
              >
                <ChevronDown size={14} />
              </button>
            </span>
          </div>
        ))}
        <p className={acceptable ? "muted" : "form-error"}>
          {total === 0 ? "Доли не заданы." : `Сумма долей ${toPercent(total)}% из 100%.`}
        </p>
      </section>

      <div className="reviews-heading">
        <div>
          <h2>Числа расчёта</h2>
        </div>
        <button className="primary-button" disabled={settingsMutation.isPending} onClick={() => settingsMutation.mutate()}>
          {settingsMutation.isPending ? "Сохраняем…" : "Сохранить числа"}
        </button>
      </div>
      <section className="fbs-settings" aria-label="Числа расчёта">
        <label>
          Резерв на брак, штук
          <input type="number" min={0} value={reserve} onChange={(event) => setReserve(Number(event.target.value))} />
        </label>
        <label>
          Приоритетных направлений
          <input type="number" min={1} value={priority} onChange={(event) => setPriority(Number(event.target.value))} />
        </label>
      </section>

      <div className="reviews-heading">
        <div>
          <h2>Объекты Wildberries</h2>
          <p className="muted">
            {setup.offices.length} объектов, без направления {setup.unassigned_offices}.
          </p>
        </div>
      </div>
      <div className="fbs-office-tools">
        <input
          aria-label="Поиск объекта"
          placeholder="Город, название или id"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <span className="mode-switch">
          {(["all", "unassigned", "used"] as OfficeFilter[]).map((value) => (
            <button
              key={value}
              className={filter === value ? "mode-active" : ""}
              onClick={() => setFilter(value)}
            >
              {value === "all" ? "Все" : value === "unassigned" ? "Без направления" : "С нашими складами"}
            </button>
          ))}
        </span>
      </div>
      <section className="fbs-office-table" aria-label="Объекты Wildberries">
        <div className="fbs-office-head">
          <span>Объект</span>
          <span>Город</span>
          <span>Округ</span>
          <span>Груз</span>
          <span>Наши склады</span>
          <span>Направление</span>
        </div>
        {offices.map((office) => (
          <div className="fbs-office-row" key={office.office_id}>
            <span>
              {office.name}
              <em className="stock-split">объект {office.office_id}</em>
            </span>
            <span>{office.city || "—"}</span>
            <span>{office.federal_district || "—"}</span>
            <span>{cargoLabel(office.cargo_type)}</span>
            <span>{office.used_by_cabinets || "—"}</span>
            <span>
              <select
                aria-label={`Направление объекта ${office.office_id}`}
                value={office.region_code ?? ""}
                onChange={(event) =>
                  officeMutation.mutate({
                    officeId: office.office_id,
                    regionCode: event.target.value || null,
                  })
                }
              >
                <option value="">без направления</option>
                {setup.regions.map((region) => (
                  <option key={region.code} value={region.code}>
                    {region.title}
                  </option>
                ))}
              </select>
            </span>
          </div>
        ))}
        {!offices.length && <div className="fbs-office-row">Ничего не нашлось.</div>}
      </section>
    </>
  );
}
