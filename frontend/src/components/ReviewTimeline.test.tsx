import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ProductReviewHistory } from "../features/reviews/types";
import { ReviewTimeline } from "./ReviewTimeline";

const product: ProductReviewHistory = { id: "p1", article: "123", name: "Товар", snapshots: Array.from({ length: 8 }, (_, index) => ({ date: `2026-08-${String(index + 1).padStart(2, "0")}`, ratings: { 1: 1, 2: 2, 3: 3, 4: 4, 5: 10 + index } })) };

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
