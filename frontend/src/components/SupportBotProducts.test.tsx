import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { BotDialog, DialogPage, Mailing, ProductGroup } from "../features/supportBot/types";
import { SupportBotProducts } from "./SupportBotProducts";

const api = vi.hoisted(() => ({
  getProducts: vi.fn(),
  getProductDialogs: vi.fn(),
  setProductSwitch: vi.fn(),
  setDialogSwitch: vi.fn(),
  setDialogFlags: vi.fn(),
}));
vi.mock("../features/supportBot/api", () => api);

const group = (patch: Partial<ProductGroup>): ProductGroup => ({
  key: 1,
  nm_id: 1,
  name: "Вакуумный упаковщик",
  vendor_code: "vac-1",
  photo_url: "",
  removed: false,
  enabled: true,
  total: 2,
  writing: 1,
  waiting: 1,
  ...patch,
});

const mailing = (patch: Partial<Mailing>): Mailing => ({
  on: true,
  source: "model",
  reason: "verdict_open",
  locked: false,
  manual_until: null,
  ...patch,
});

const dialog = (chat: string, patch: Partial<BotDialog>): BotDialog => ({
  chat_id: chat,
  prompt_at: "2026-11-08T06:00:00+00:00",
  state: "in_campaign",
  nm_id: 1,
  buyer_first_at: null,
  buyer_last_at: null,
  our_last_at: null,
  campaign_started_at: null,
  next_send_at: null,
  sent_count: 3,
  last_sent_at: null,
  no_contact: false,
  attention: false,
  excluded: false,
  closed: false,
  chat_url: null,
  verdict: "open",
  verdict_reason: "просит выбрать вариант",
  reply_hold: false,
  reply_category: null,
  mailing: mailing({}),
  ...patch,
});

function page(dialogs: BotDialog[]): DialogPage {
  return { dialogs, total: dialogs.length, page: 1, page_size: 50 };
}

function mount() {
  const onError = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SupportBotProducts sellerId="seller-1" onError={onError} />
    </QueryClientProvider>,
  );
  return onError;
}

beforeEach(() => {
  vi.clearAllMocks();
  api.setProductSwitch.mockResolvedValue(undefined);
  api.setDialogSwitch.mockResolvedValue(undefined);
  api.setDialogFlags.mockResolvedValue(undefined);
  api.getProducts.mockResolvedValue({
    scope: "campaign",
    manual_hours: 24,
    products: [
      group({}),
      group({ key: 3, nm_id: 3, name: "Воздуходувка", enabled: false, writing: 0, total: 4, waiting: 4 }),
    ],
  });
  api.getProductDialogs.mockImplementation(async (_seller: string, key: number) =>
    page(
      key === 1
        ? [
            dialog("1:open", {}),
            dialog("1:closing", {
              verdict: "closing",
              verdict_reason: "заявка одобрена",
              mailing: mailing({ on: false, reason: "verdict_closing" }),
            }),
          ]
        : [dialog("1:off", { mailing: mailing({ on: false, source: "product", reason: "product_off", locked: true }) })],
    ),
  );
});

describe("SupportBotProducts", () => {
  it("показывает товары по порядку сервера, выключенный свёрнут, включённый с пишущими диалогами раскрыт", async () => {
    mount();

    const heads = await screen.findAllByRole("button", { expanded: undefined, name: /Вакуумный|Воздуходувка/ });
    expect(heads.map((head) => head.getAttribute("aria-expanded"))).toEqual(["true", "false"]);
    expect(await screen.findByText("пишем 1 из 2")).toBeInTheDocument();
    expect(screen.getByText("выключен, диалогов 4")).toBeInTheDocument();
    expect(screen.getByText(/Пишем сейчас по 1 диалогам · выключено товаров: 1 из 2/)).toBeInTheDocument();
    await waitFor(() => expect(api.getProductDialogs).toHaveBeenCalledTimes(1));
    expect(api.getProductDialogs).toHaveBeenCalledWith("seller-1", 1, "campaign", 1);
  });

  it("тумблер товара шлёт запрос на выключение и не раскрывает и не сворачивает группу", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(await screen.findByRole("switch", { name: /Выключить рассылку по товару Вакуумный/ }));

    expect(api.setProductSwitch).toHaveBeenCalledWith("seller-1", 1, false);
    const head = screen.getByRole("button", { name: /Вакуумный упаковщик/ });
    expect(head).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: /Воздуходувка/ })).toHaveAttribute("aria-expanded", "false");
  });

  it("тумблер диалога повторяет решение модели и переключается в обе стороны", async () => {
    const user = userEvent.setup();
    mount();

    const on = await screen.findByRole("switch", { name: "Выключить рассылку по диалогу" });
    const off = screen.getByRole("switch", { name: "Включить рассылку по диалогу" });
    expect(on).toHaveAttribute("aria-checked", "true");
    expect(off).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText("ИИ: диалог завершён (заявка одобрена)")).toBeInTheDocument();

    await user.click(off);
    await user.click(on);

    expect(api.setDialogSwitch).toHaveBeenNthCalledWith(1, "seller-1", expect.objectContaining({ chat_id: "1:closing" }), true);
    expect(api.setDialogSwitch).toHaveBeenNthCalledWith(2, "seller-1", expect.objectContaining({ chat_id: "1:open" }), false);
  });

  it("у ручного решения видна подпись со сроком и кнопка возврата к модели", async () => {
    const user = userEvent.setup();
    api.getProductDialogs.mockResolvedValue(
      page([
        dialog("1:manual", {
          mailing: mailing({ on: true, source: "manual", reason: "manual_on", manual_until: "2026-11-09T12:20:00+00:00" }),
        }),
      ]),
    );
    mount();

    expect(await screen.findByText(/включён вручную до 09\.11/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "вернуть к решению модели" }));

    expect(api.setDialogSwitch).toHaveBeenCalledWith("seller-1", expect.objectContaining({ chat_id: "1:manual" }), null);
  });

  it("заблокированный тумблер недоступен, а в выключенном товаре диалоги помечены", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(await screen.findByRole("button", { name: /Воздуходувка/ }));

    const rows = await screen.findAllByText("выключен товаром");
    expect(rows.length).toBeGreaterThan(0);
    const locked = within(rows[0].closest(".bot-prod-row") as HTMLElement).getByRole("switch");
    expect(locked).toBeDisabled();
  });

  it("при ошибке сервера сообщает её, а не молчит", async () => {
    const user = userEvent.setup();
    api.setProductSwitch.mockRejectedValue(new Error("boom"));
    const onError = mount();

    await user.click(await screen.findByRole("switch", { name: /Выключить рассылку по товару Вакуумный/ }));

    await waitFor(() => expect(onError).toHaveBeenCalledWith("Не удалось переключить товар"));
  });

  it("переключение набора диалогов запрашивает товары заново", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByText("пишем 1 из 2");

    await user.click(screen.getByRole("tab", { name: "Все диалоги" }));

    await waitFor(() => expect(api.getProducts).toHaveBeenCalledWith("seller-1", "all"));
  });
});
