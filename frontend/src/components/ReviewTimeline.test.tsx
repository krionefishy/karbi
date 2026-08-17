import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { histories } from "../mocks/data";
import { ReviewTimeline } from "./ReviewTimeline";

describe("ReviewTimeline", () => {
  it("calculates the five-star delta from adjacent snapshots", () => {
    render(<ReviewTimeline products={[histories["fashion-house"][0]]} />);

    expect(screen.getAllByText("+6").length).toBeGreaterThan(0);
  });

  it("opens the full rating breakdown for a selected day", () => {
    render(<ReviewTimeline products={[histories["fashion-house"][0]]} />);

    fireEvent.click(screen.getAllByRole("button", { expanded: false })[0]);
    expect(screen.getByText(/Всего отзывов:/)).toBeInTheDocument();
    expect(screen.getByText("1", { selector: ".rating-row span" })).toBeInTheDocument();
  });
});
