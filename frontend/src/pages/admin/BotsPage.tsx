import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { ApiError } from "../../api/http";
import { Shell } from "../../components/Shell";
import { BotDialog } from "../../components/BotDialog";
import { createBot, deleteBot, getBots } from "../../features/admin/api";
import type { BotDelivery } from "../../features/admin/types";

const moment = (value: string) =>
  new Date(value).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

/** Отказы после последней доставки — признак сломанного канала, а не одного заблокированного чата. */
function DeliveryState({ delivery }: { delivery: BotDelivery }) {
  if (delivery.failed_count > 0) {
    return (
      <span className="form-error" role="alert">
        Не доставлено {delivery.failed_count}
        {delivery.failed_since ? ` с ${moment(delivery.failed_since)}` : ""}
        {delivery.last_error ? `: ${delivery.last_error}` : ""}
      </span>
    );
  }
  if (delivery.last_sent_at) return <span className="muted">Последнее — {moment(delivery.last_sent_at)}</span>;
  return <span className="muted">Ещё ничего не отправлял</span>;
}

export function BotsPage() {
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState("");
  const [rowError, setRowError] = useState("");

  const { data: bots = [], isLoading } = useQuery({ queryKey: ["bots"], queryFn: getBots });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["bots"] });

  const createMutation = useMutation({
    mutationFn: createBot,
    onSuccess: async () => {
      setCreating(false);
      setFormError("");
      await refresh();
    },
    onError: (error) => setFormError(error instanceof ApiError ? error.message : "Не удалось подключить бота"),
  });
  const deleteMutation = useMutation({
    mutationFn: deleteBot,
    onSuccess: async () => {
      setRowError("");
      await refresh();
    },
    onError: (error) => setRowError(error instanceof ApiError ? error.message : "Не удалось отключить бота"),
  });

  return (
    <Shell admin>
      <main className="page-container">
        <div className="page-heading">
          <div>
            <h1>Боты</h1>
            <p className="muted">
              Через них уходят уведомления селлерам. Токены хранятся в сервисе доставки за пределами РФ и в
              эту систему не попадают.
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
              Подключить бота
            </button>
          </div>
        </div>

        {rowError && (
          <p className="form-error" role="alert">
            {rowError}
          </p>
        )}

        {isLoading ? (
          <p className="muted">Загружаем…</p>
        ) : bots.length === 0 ? (
          <p className="muted">
            Ботов пока нет. Пока не подключён ни один, уведомления селлерам отправлять нечем.
          </p>
        ) : (
          <table className="registry-table">
            <thead>
              <tr>
                <th>Код</th>
                <th>Название</th>
                <th>Ссылка-приглашение</th>
                <th>Доставка</th>
                <th aria-label="Действия" />
              </tr>
            </thead>
            <tbody>
              {bots.map((bot) => (
                <tr key={bot.id}>
                  <td>
                    <strong>{bot.code}</strong>
                  </td>
                  <td>{bot.title || "—"}</td>
                  <td className="muted">{bot.invite_link_template.replace("{token}", "…")}</td>
                  <td>
                    <DeliveryState delivery={bot.delivery} />
                  </td>
                  <td>
                    <span className="registry-row-actions">
                      <button
                        className="danger-icon"
                        aria-label={`Отключить ${bot.code}`}
                        disabled={deleteMutation.isPending}
                        onClick={() => {
                          // Deleting takes the token with it on the relay, and the
                          // subscriptions here: there is no undo worth pretending about.
                          if (window.confirm(`Отключить бота ${bot.code}? Подписки будут потеряны.`)) {
                            deleteMutation.mutate(bot.code);
                          }
                        }}
                      >
                        <Trash2 size={16} />
                      </button>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </main>

      {creating && (
        <BotDialog
          pending={createMutation.isPending}
          error={formError}
          onClose={() => setCreating(false)}
          onSubmit={(value) => createMutation.mutate(value)}
        />
      )}
    </Shell>
  );
}
