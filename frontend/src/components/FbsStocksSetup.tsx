import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronUp, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { ApiError } from "../api/http";
import {
  addGroup,
  deleteGroup,
  reorderGroups,
  setGroupColumns,
  updateGroup,
} from "../features/fbsStocks/api";
import { getSetup } from "../features/fbsStocks/api";
import { GROUP_KIND_LABELS, type GroupKind, type StocksGroup, type WarehouseSetup } from "../features/fbsStocks/types";

interface Props {
  sellerId: string;
}

const HIDDEN = "";
const FBS = 1;

/**
 * Столбцы таблицы: группы в её порядке и склады кабинета, разложенные по группам.
 * DBS и EDBS спрятаны по умолчанию — в таблице остатков они селлеру не нужны.
 */
export function FbsStocksSetup({ sellerId }: Props) {
  const queryClient = useQueryClient();
  const { data: setup, isLoading } = useQuery({ queryKey: ["fbs-stocks-setup", sellerId], queryFn: () => getSetup(sellerId) });
  const [error, setError] = useState("");
  const [newTitle, setNewTitle] = useState("");
  const [newKind, setNewKind] = useState<GroupKind>("district");
  const [showAll, setShowAll] = useState(false);

  const done = async () => {
    setError("");
    await queryClient.invalidateQueries({ queryKey: ["fbs-stocks-setup", sellerId] });
    await queryClient.invalidateQueries({ queryKey: ["fbs-stocks", sellerId] });
    await queryClient.invalidateQueries({ queryKey: ["fbs-stocks-boards"] });
  };
  const fail = (fallback: string) => (raised: unknown) =>
    setError(raised instanceof ApiError ? raised.message : fallback);

  const addMutation = useMutation({
    mutationFn: () => addGroup(sellerId, { title: newTitle, kind: newKind }),
    onSuccess: async () => {
      setNewTitle("");
      await done();
    },
    onError: fail("Не удалось добавить группу"),
  });
  const updateMutation = useMutation({
    mutationFn: ({ group, title, kind }: { group: StocksGroup; title: string; kind: GroupKind }) =>
      updateGroup(sellerId, group.id, { title, kind }),
    onSuccess: done,
    onError: fail("Не удалось сохранить группу"),
  });
  const deleteMutation = useMutation({
    mutationFn: (group: StocksGroup) => deleteGroup(sellerId, group.id),
    onSuccess: done,
    onError: fail("Не удалось удалить группу"),
  });
  const orderMutation = useMutation({
    mutationFn: (ids: string[]) => reorderGroups(sellerId, ids),
    onSuccess: done,
    onError: fail("Не удалось переставить группы"),
  });
  const columnsMutation = useMutation({
    mutationFn: ({ groupId, warehouseIds }: { groupId: string; warehouseIds: number[] }) =>
      setGroupColumns(sellerId, groupId, warehouseIds),
    onSuccess: done,
    onError: fail("Не удалось переставить склад"),
  });

  if (isLoading || !setup) return <div className="loading-block">Загружаем склады кабинета…</div>;

  // Состав группы уезжает на сервер целиком и строится из кэша: пока правка не
  // записана и список не перечитан, вторая правка собрала бы его из старых данных.
  const busy =
    columnsMutation.isPending || orderMutation.isPending || updateMutation.isPending || deleteMutation.isPending;
  const groups = setup.groups;
  const membersOf = (groupId: string) =>
    setup.warehouses
      .filter((warehouse) => warehouse.group_id === groupId)
      .sort((left, right) => left.position - right.position)
      .map((warehouse) => warehouse.warehouse_id);

  const moveGroup = (index: number, delta: number) => {
    const ids = groups.map((group) => group.id);
    const target = index + delta;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    orderMutation.mutate(ids);
  };
  const place = (warehouse: WarehouseSetup, groupId: string) => {
    if (groupId === HIDDEN) {
      if (!warehouse.group_id) return;
      columnsMutation.mutate({
        groupId: warehouse.group_id,
        warehouseIds: membersOf(warehouse.group_id).filter((id) => id !== warehouse.warehouse_id),
      });
      return;
    }
    columnsMutation.mutate({ groupId, warehouseIds: [...membersOf(groupId), warehouse.warehouse_id] });
  };
  const moveWarehouse = (warehouse: WarehouseSetup, delta: number) => {
    if (!warehouse.group_id) return;
    const ids = membersOf(warehouse.group_id);
    const index = ids.indexOf(warehouse.warehouse_id);
    const target = index + delta;
    if (index < 0 || target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    columnsMutation.mutate({ groupId: warehouse.group_id, warehouseIds: ids });
  };

  const warehouses = setup.warehouses
    .filter((warehouse) => showAll || warehouse.delivery_type === FBS || warehouse.group_id)
    .sort((left, right) => left.name.localeCompare(right.name, "ru"));

  return (
    <>
      {error && <div className="inline-error">{error}</div>}

      <section className="card" aria-label="Группы столбцов">
        <div className="card-head">
          <div className="card-head-title">
            <h2>Группы</h2>
            <span className="muted">в этом порядке они идут слева направо</span>
          </div>
        </div>
        {groups.map((group, index) => (
          <div className="fbs-region-row stocks-group-row" key={group.id}>
            <span className="fbs-region-place">{index + 1}</span>
            <input
              key={`${group.id}:${group.title}`}
              aria-label={`Название группы ${group.title}`}
              defaultValue={group.title}
              maxLength={255}
              onBlur={(event) => {
                const title = event.target.value.trim();
                if (title && title !== group.title) updateMutation.mutate({ group, title, kind: group.kind });
              }}
            />
            <select
              aria-label={`Вид группы ${group.title}`}
              value={group.kind}
              disabled={busy}
              onChange={(event) => updateMutation.mutate({ group, title: group.title, kind: event.target.value as GroupKind })}
            >
              {(Object.keys(GROUP_KIND_LABELS) as GroupKind[]).map((kind) => (
                <option key={kind} value={kind}>
                  {GROUP_KIND_LABELS[kind]}
                </option>
              ))}
            </select>
            <span className="muted">{group.columns.length} скл.</span>
            <span className="fbs-region-move">
              <button aria-label={`Поднять ${group.title}`} onClick={() => moveGroup(index, -1)} disabled={busy || index === 0}>
                <ChevronUp size={14} />
              </button>
              <button
                aria-label={`Опустить ${group.title}`}
                onClick={() => moveGroup(index, 1)}
                disabled={busy || index === groups.length - 1}
              >
                <ChevronDown size={14} />
              </button>
              <button
                aria-label={`Удалить ${group.title}`}
                disabled={busy}
                onClick={() => {
                  if (window.confirm(`Удалить группу «${group.title}»? Её склады перестанут показываться.`)) {
                    deleteMutation.mutate(group);
                  }
                }}
              >
                <Trash2 size={14} />
              </button>
            </span>
          </div>
        ))}
        <form
          className="stocks-new-group"
          onSubmit={(event) => {
            event.preventDefault();
            if (newTitle.trim()) addMutation.mutate();
          }}
        >
          <input
            aria-label="Название новой группы"
            placeholder="Новая группа, например «Уральский округ»"
            value={newTitle}
            maxLength={255}
            onChange={(event) => setNewTitle(event.target.value)}
          />
          <select aria-label="Вид новой группы" value={newKind} onChange={(event) => setNewKind(event.target.value as GroupKind)}>
            {(Object.keys(GROUP_KIND_LABELS) as GroupKind[]).map((kind) => (
              <option key={kind} value={kind}>
                {GROUP_KIND_LABELS[kind]}
              </option>
            ))}
          </select>
          <button className="secondary-button" type="submit" disabled={addMutation.isPending || !newTitle.trim()}>
            <Plus size={15} /> Добавить группу
          </button>
        </form>
        <p className="card-foot muted">
          «Наш склад» раскрывается на листе сравнения по складам, округа там стоят суммами; фулфилмент в сравнение не идёт.
        </p>
      </section>

      <section className="card" aria-label="Склады кабинета">
        <div className="card-head">
          <div className="card-head-title">
            <h2>Склады кабинета</h2>
            <span className="muted">{setup.warehouses.length} в кабинете WB</span>
          </div>
          <label className="dormant-toggle" style={{ margin: 0 }}>
            <input type="checkbox" checked={showAll} onChange={(event) => setShowAll(event.target.checked)} />
            Показать DBS и EDBS
          </label>
        </div>
        {setup.warehouses.length === 0 && (
          <span className="fbs-row-empty">Склады появятся после первого обновления — нажмите «Обновить данные».</span>
        )}
        {warehouses.map((warehouse) => {
          const siblings = warehouse.group_id ? membersOf(warehouse.group_id) : [];
          const index = siblings.indexOf(warehouse.warehouse_id);
          return (
            <div className="stocks-warehouse-row" key={warehouse.warehouse_id}>
              <span>
                <strong>{warehouse.name}</strong>
                <em className="muted">
                  {warehouse.warehouse_id}
                  {warehouse.delivery_type !== FBS && " · не FBS"}
                  {warehouse.is_deleting && " · удаляется"}
                </em>
              </span>
              <select
                aria-label={`Группа склада ${warehouse.name}`}
                value={warehouse.group_id ?? HIDDEN}
                disabled={busy}
                onChange={(event) => place(warehouse, event.target.value)}
              >
                <option value={HIDDEN}>Не показывать</option>
                {groups.map((group) => (
                  <option key={group.id} value={group.id}>
                    {group.title}
                  </option>
                ))}
              </select>
              <span className="fbs-region-move">
                <button
                  aria-label={`Левее ${warehouse.name}`}
                  onClick={() => moveWarehouse(warehouse, -1)}
                  disabled={busy || !warehouse.group_id || index <= 0}
                >
                  <ChevronUp size={14} />
                </button>
                <button
                  aria-label={`Правее ${warehouse.name}`}
                  onClick={() => moveWarehouse(warehouse, 1)}
                  disabled={busy || !warehouse.group_id || index < 0 || index >= siblings.length - 1}
                >
                  <ChevronDown size={14} />
                </button>
              </span>
            </div>
          );
        })}
      </section>
    </>
  );
}
