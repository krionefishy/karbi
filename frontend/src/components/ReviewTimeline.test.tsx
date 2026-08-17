import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ProductReviewHistory } from "../features/reviews/types";
import { ReviewTimeline } from "./ReviewTimeline";

const today = new Date();
const product: ProductReviewHistory = { id: "p1", article: "123", name: "Товар", snapshots: Array.from({ length: 8 }, (_, index) => {
  const date = new Date(today);
  date.setUTCDate(today.getUTCDate() - 7 + index);
  return { date: date.toISOString().slice(0, 10), ratings: { 1: 1, 2: 2, 3: 3, 4: 4, 5: 10 + index } };
}) };

describe("ReviewTimeline", () => {
  it("calculates the five-star delta from adjacent snapshots", () => {
    render(<ReviewTimeline products={[product]} />);
    expect(screen.getAllByText("+1").length).toBeGreaterThan(0);
  });

  it("opens the full rating breakdown for a selected day", () => {
    render(<ReviewTimeline products={[product]} />);
    fireEvent.click(screen.getAllByRole("button", { expanded: false })[0]);
    expect(screen.getByText(/Всего отзывов:/)).toBeInTheDocument();
  });
});
