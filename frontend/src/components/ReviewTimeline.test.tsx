import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ProductReviewHistory } from "../features/reviews/types";
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
    const { container } = render(<ReviewTimeline products={[todayOnly]} />);

    expect(screen.getByRole("slider")).toHaveAttribute("max", "83");
    expect(container.querySelector(".timeline-head > div:last-child")).toHaveTextContent("Сегодня");

    fireEvent.click(screen.getByRole("button", { name: "Предыдущие семь дней" }));
    expect(screen.getAllByText("Нет данных")).toHaveLength(7);
  });

  it("calculates the delta of the total review count from adjacent snapshots", () => {
    render(<ReviewTimeline products={[product]} />);
    expect(screen.getAllByText("+1").length).toBeGreaterThan(0);
    // Ratings sum to 20 + index, and the window shows indexes 1..7.
    expect(screen.getByText("27")).toBeInTheDocument();
  });

  it("opens the full rating breakdown for a selected day", () => {
    render(<ReviewTimeline products={[product]} />);
    fireEvent.click(screen.getAllByRole("button", { name: /Отзывы за/ })[0]);
    expect(screen.getByText(/Всего отзывов:/)).toBeInTheDocument();
    expect(screen.getByText(/По карточке целиком:/)).toBeInTheDocument();
  });

  it("shows the whole card in the panel, including what the row had to cut", () => {
    render(<ReviewTimeline products={[product]} latestDate={snapshots.at(-1)!.date} />);
    fireEvent.click(screen.getByRole("button", { name: /Карточка и динамика за 30 дней/ }));

    // The row truncates the title and hides the склейка; the panel must not.
    expect(screen.getByRole("heading", { name: "Товар" })).toBeInTheDocument();
    expect(screen.getByText("Карточка (склейка)").nextElementSibling).toHaveTextContent("999");
    expect(screen.getByText("Артикул продавца").nextElementSibling).toHaveTextContent("SKU-123");
    expect(screen.getByText("Предмет").nextElementSibling).toHaveTextContent("Перфораторы");
    expect(screen.getByText("Состояние").nextElementSibling).toHaveTextContent("В продаже");
  });

  it("opens the 30-day chart from the product row", () => {
    render(<ReviewTimeline products={[product]} latestDate={snapshots.at(-1)!.date} />);

    const toggle = screen.getByRole("button", { name: /Карточка и динамика за 30 дней/ });
    expect(screen.queryByText(/Динамика за 30 дней/)).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.getByText(/Динамика за 30 дней/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Изменение количества отзывов/ })).toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.queryByText(/Динамика за 30 дней/)).not.toBeInTheDocument();
  });

  it("shows the chart and a day's breakdown one at a time", () => {
    render(<ReviewTimeline products={[product]} latestDate={snapshots.at(-1)!.date} />);

    fireEvent.click(screen.getByRole("button", { name: /Карточка и динамика за 30 дней/ }));
    expect(screen.getByText(/Динамика за 30 дней/)).toBeInTheDocument();

    // Picking a day replaces the chart rather than stacking two panels.
    fireEvent.click(screen.getAllByRole("button", { name: /Отзывы за/ })[0]);
    expect(screen.queryByText(/Динамика за 30 дней/)).not.toBeInTheDocument();
    expect(screen.getByText(/Всего отзывов:/)).toBeInTheDocument();
  });

  it("marks a product whose review count moved overnight", () => {
    const latest = snapshots.at(-1)!.date;
    render(<ReviewTimeline products={[product]} latestDate={latest} />);

    // The fixture grows the five-star count by one each day, so the newest day
    // sits one review above the one before it.
    expect(screen.getByText(/\+1 за сутки/)).toBeInTheDocument();
  });

  it("says nothing about products that did not move", () => {
    const flat = {
      ...product,
      snapshots: product.snapshots.map((snapshot) => ({ ...snapshot, ratings: { 1: 0, 2: 0, 3: 0, 4: 0, 5: 7 } })),
    };
    render(<ReviewTimeline products={[flat]} latestDate={flat.snapshots.at(-1)!.date} />);

    expect(screen.queryByText(/за сутки/)).not.toBeInTheDocument();
  });

  it("shows how many five-star reviews are still missing, per article and per card", () => {
    render(<ReviewTimeline products={[product]} />);
    fireEvent.click(screen.getAllByRole("button", { name: /Отзывы за/ })[0]);

    // Which day opens depends on the Moscow date, so the arithmetic itself is
    // covered in rating.test.ts; here we only check both rows are wired up.
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
    render(<ReviewTimeline products={[perfect]} />);
    fireEvent.click(screen.getAllByRole("button", { name: /Отзывы за/ })[0]);
    expect(screen.getByText("оценка 5,0 достигнута")).toBeInTheDocument();
  });

  it("labels an article that WB no longer lists", () => {
    render(<ReviewTimeline products={[{ ...product, state: "feedback_only" }]} />);
    expect(screen.getByText("Нет в каталоге")).toBeInTheDocument();
  });
});
