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
    fireEvent.click(screen.getAllByRole("button", { expanded: false })[0]);
    expect(screen.getByText(/Всего отзывов:/)).toBeInTheDocument();
    expect(screen.getByText(/По карточке целиком:/)).toBeInTheDocument();
  });

  it("shows how many five-star reviews are still missing, per article and per card", () => {
    render(<ReviewTimeline products={[product]} />);
    fireEvent.click(screen.getAllByRole("button", { expanded: false })[0]);

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
    fireEvent.click(screen.getAllByRole("button", { expanded: false })[0]);
    expect(screen.getByText("оценка 5,0 достигнута")).toBeInTheDocument();
  });

  it("labels an article that WB no longer lists", () => {
    render(<ReviewTimeline products={[{ ...product, state: "feedback_only" }]} />);
    expect(screen.getByText("Нет в каталоге")).toBeInTheDocument();
  });
});
