import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ProductReviewHistory } from "../features/reviews/types";
import { buildGroups } from "../features/reviews/groups";
import { ReviewTimeline } from "./ReviewTimeline";

const today = new Date();
const snapshots = Array.from({ length: 8 }, (_, index) => {
  const date = new Date(today);
  date.setUTCDate(today.getUTCDate() - 7 + index);
  return { date: date.toISOString().slice(0, 10), ratings: { 1: 1, 2: 2, 3: 3, 4: 4, 5: 10 + index } };
});
const product: ProductReviewHistory = {
  id: "p1",
  article: "123",
  vendor_code: "SKU-123",
  name: "Товар",
  imt_id: 999,
  brand: "Бренд",
  subject_id: 1166,
  subject_name: "Перфораторы",
  photo_url: "",
  state: "active",
  snapshots,
  card_snapshots: snapshots.map((snapshot) => ({
    date: snapshot.date,
    ratings: { ...snapshot.ratings, 5: snapshot.ratings[5] * 2 },
  })),
  photo_count: 12,
  rating: 4.9,
  rating_source: "wb",
  group_id: null,
  group_role: null,
  group_manual: false,
  group_snapshots: [],
};

describe("ReviewTimeline", () => {
  it("keeps a 90-day calendar even when only today has data", () => {
    const parts = new Intl.DateTimeFormat("en-CA", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      timeZone: "Europe/Moscow",
    }).formatToParts(new Date());
    const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    const todayOnly = {
      ...product,
      snapshots: [{ date: `${values.year}-${values.month}-${values.day}`, ratings: product.snapshots[0].ratings }],
      card_snapshots: [],
    };
    const { container } = render(<ReviewTimeline groups={buildGroups([todayOnly])} />);

    expect(screen.getByRole("slider")).toHaveAttribute("max", "83");
    expect(container.querySelector(".timeline-head > div:last-child")).toHaveTextContent("Сегодня");

    fireEvent.click(screen.getByRole("button", { name: "Предыдущие семь дней" }));
    expect(screen.getAllByText("Нет данных")).toHaveLength(7);
  });

  it("calculates the delta of the total review count from adjacent snapshots", () => {
    render(<ReviewTimeline groups={buildGroups([product])} />);
    expect(screen.getAllByText("+1").length).toBeGreaterThan(0);
    expect(screen.getByText("27")).toBeInTheDocument();
  });

  it("opens the full rating breakdown for a selected day", () => {
    render(<ReviewTimeline groups={buildGroups([product])} />);
    fireEvent.click(screen.getAllByRole("button", { name: /Отзывы за/ })[0]);
    expect(screen.getByText(/Всего отзывов:/)).toBeInTheDocument();
    expect(screen.getByText(/По карточке целиком:/)).toBeInTheDocument();
  });

  it("shows the whole card in the panel, including what the row had to cut", () => {
    render(<ReviewTimeline groups={buildGroups([product])} latestDate={snapshots.at(-1)!.date} />);
    fireEvent.click(screen.getByRole("button", { name: /Карточка и динамика за 30 дней/ }));

    expect(screen.getByRole("heading", { name: "Товар" })).toBeInTheDocument();
    expect(screen.getByText("Карточка (склейка)").nextElementSibling).toHaveTextContent("999");
    expect(screen.getByText("Артикул продавца").nextElementSibling).toHaveTextContent("SKU-123");
    expect(screen.getByText("Предмет").nextElementSibling).toHaveTextContent("Перфораторы");
    expect(screen.getByText("Состояние").nextElementSibling).toHaveTextContent("В продаже");
  });

  it("opens the 30-day chart from the product row", () => {
    render(<ReviewTimeline groups={buildGroups([product])} latestDate={snapshots.at(-1)!.date} />);

    const toggle = screen.getByRole("button", { name: /Карточка и динамика за 30 дней/ });
    expect(screen.queryByText(/Динамика за 30 дней/)).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.getByText(/Динамика за 30 дней/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Изменение количества отзывов/ })).toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.queryByText(/Динамика за 30 дней/)).not.toBeInTheDocument();
  });

  it("shows the chart and a day's breakdown one at a time", () => {
    render(<ReviewTimeline groups={buildGroups([product])} latestDate={snapshots.at(-1)!.date} />);

    fireEvent.click(screen.getByRole("button", { name: /Карточка и динамика за 30 дней/ }));
    expect(screen.getByText(/Динамика за 30 дней/)).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: /Отзывы за/ })[0]);
    expect(screen.queryByText(/Динамика за 30 дней/)).not.toBeInTheDocument();
    expect(screen.getByText(/Всего отзывов:/)).toBeInTheDocument();
  });

  it("marks a product whose review count moved overnight", () => {
    const latest = snapshots.at(-1)!.date;
    render(<ReviewTimeline groups={buildGroups([product])} latestDate={latest} />);

    expect(screen.getByText(/\+1 за сутки/)).toBeInTheDocument();
  });

  it("says nothing about products that did not move", () => {
    const flat = {
      ...product,
      snapshots: product.snapshots.map((snapshot) => ({ ...snapshot, ratings: { 1: 0, 2: 0, 3: 0, 4: 0, 5: 7 } })),
    };
    render(<ReviewTimeline groups={buildGroups([flat])} latestDate={flat.snapshots.at(-1)!.date} />);

    expect(screen.queryByText(/за сутки/)).not.toBeInTheDocument();
  });

  it("shows how many five-star reviews are still missing, per article and per card", () => {
    render(<ReviewTimeline groups={buildGroups([product])} />);
    fireEvent.click(screen.getAllByRole("button", { name: /Отзывы за/ })[0]);

    expect(screen.getByText("По артикулу")).toBeInTheDocument();
    expect(screen.getByText("По карточке")).toBeInTheDocument();
    expect(screen.getAllByText(/до 5,0 — ещё/)).toHaveLength(2);
  });

  it("reports a distribution that already displays as 5.0", () => {
    const perfect = {
      ...product,
      snapshots: product.snapshots.map((snapshot) => ({ ...snapshot, ratings: { 1: 0, 2: 0, 3: 0, 4: 0, 5: 50 } })),
      card_snapshots: [],
    };
    render(<ReviewTimeline groups={buildGroups([perfect])} />);
    fireEvent.click(screen.getAllByRole("button", { name: /Отзывы за/ })[0]);
    expect(screen.getByText("оценка 5,0 достигнута")).toBeInTheDocument();
  });

  it("labels an article that WB no longer lists", () => {
    render(<ReviewTimeline groups={buildGroups([{ ...product, state: "feedback_only" }])} />);
    expect(screen.getByText("Нет в каталоге")).toBeInTheDocument();
  });

  describe("groups of a main product and its dummies", () => {
    const sum = (index: number) => snapshots[index].ratings;
    const main: ProductReviewHistory = {
      ...product,
      group_id: "imt:999",
      group_role: "main",
      group_snapshots: snapshots.map((snapshot) => ({
        date: snapshot.date,
        ratings: { ...snapshot.ratings, 5: snapshot.ratings[5] + 1000 },
      })),
    };
    const dummy: ProductReviewHistory = {
      ...product,
      id: "p2",
      article: "456",
      vendor_code: "шуруповёрт1",
      name: "Болванка",
      photo_count: 0,
      rating: 4.9,
      rating_source: "group",
      group_id: "imt:999",
      group_role: "member",
    };
    const today = snapshots.at(-1)!.date;

    it("collapses dummies into the main row and shows the group total", () => {
      render(<ReviewTimeline groups={buildGroups([dummy, main])} latestDate={today} />);

      expect(screen.queryByText("Болванка")).not.toBeInTheDocument();
      expect(screen.getByText("+1 болванка")).toBeInTheDocument();
      const total = 1 + 2 + 3 + 4 + sum(7)[5] + 1000;
      const cells = screen.getAllByRole("button", { name: /Отзывы группы за/ });
      expect(cells.at(-1)!.textContent?.replace(/\s/g, "")).toContain(String(total));
      expect(screen.getAllByRole("button", { name: /Отзывы группы за/ }).length).toBeGreaterThan(0);
    });

    it("expands the group to show each dummy with its own counts", () => {
      render(<ReviewTimeline groups={buildGroups([dummy, main])} latestDate={today} />);
      fireEvent.click(screen.getByRole("button", { name: /Показать болванки: Товар/ }));

      expect(screen.getByText("Болванка")).toBeInTheDocument();
      expect(screen.getByText("без фото")).toBeInTheDocument();
      expect(screen.queryAllByRole("button", { name: /Отзывы группы за/ })).toHaveLength(0);
    });

    it("puts the WB rating next to the name and marks the inherited one", () => {
      render(<ReviewTimeline groups={buildGroups([dummy, main])} latestDate={today} />);
      fireEvent.click(screen.getByRole("button", { name: "Показать болванки" }));

      expect(screen.getByTitle("Рейтинг WB")).toHaveTextContent("4,9");
      expect(screen.getByTitle(/Рейтинг склейки/)).toHaveTextContent("4,9");
    });

    it("marks a rating we computed ourselves", () => {
      render(<ReviewTimeline groups={buildGroups([{ ...product, rating: 4.87, rating_source: "reviews" }])} />);
      expect(screen.getByTitle(/WB не отдал рейтинг/)).toHaveTextContent("≈4,87");
    });

    it("pins a dummy as the main product from the row menu", () => {
      const onGroupAction = vi.fn();
      render(<ReviewTimeline groups={buildGroups([dummy, main])} latestDate={today} onGroupAction={onGroupAction} />);
      fireEvent.click(screen.getByRole("button", { name: /Показать болванки: Товар/ }));
      fireEvent.click(screen.getByRole("button", { name: "Группа товара 456" }));
      fireEvent.click(screen.getByRole("menuitem", { name: "Сделать основным в группе" }));

      expect(onGroupAction).toHaveBeenCalledWith("456", "pin_main");
    });

    it("attaches a lone dummy to a chosen main product", () => {
      const onGroupAction = vi.fn();
      const lone: ProductReviewHistory = { ...dummy, id: "p3", article: "789", group_id: null, group_role: null };
      const other: ProductReviewHistory = { ...product, id: "p4", article: "111", name: "Другой основной" };
      render(<ReviewTimeline groups={buildGroups([main, dummy, lone, other])} onGroupAction={onGroupAction} />);
      fireEvent.click(screen.getByRole("button", { name: "Группа товара 789" }));
      fireEvent.click(screen.getByRole("menuitem", { name: /Привязать к основному/ }));
      fireEvent.click(screen.getByRole("radio", { name: /Другой основной/ }));
      fireEvent.click(screen.getByRole("button", { name: "Привязать" }));

      expect(onGroupAction).toHaveBeenCalledWith("789", "attach", "111");
    });
  });
});
