import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DailyReviewSnapshot } from "../features/reviews/types";
import { ReviewChart } from "./ReviewChart";

const END = "2026-08-25";

function day(date: string, total: number): DailyReviewSnapshot {
  return { date, ratings: { 1: 0, 2: 0, 3: 0, 4: 0, 5: total } };
}

/** Thirty consecutive days ending on END, growing by `step` a day. */
function climbing(from: number, step: number): DailyReviewSnapshot[] {
  return Array.from({ length: 30 }, (_, index) => {
    const cursor = new Date(`${END}T00:00:00Z`);
    cursor.setUTCDate(cursor.getUTCDate() - (29 - index));
    return day(cursor.toISOString().slice(0, 10), from + index * step);
  });
}

function pointsOf(path: string): Array<{ x: number; y: number }> {
  return [...path.matchAll(/[ML](-?[\d.]+) (-?[\d.]+)/g)].map((match) => ({
    x: Number(match[1]),
    y: Number(match[2]),
  }));
}

function linePath(container: HTMLElement): string {
  return container.querySelector(".chart-line")?.getAttribute("d") ?? "";
}

describe("ReviewChart", () => {
  it("draws one point per collected day, left to right", () => {
    const { container } = render(<ReviewChart snapshots={climbing(1289, 2)} cardSnapshots={[]} endDate={END} />);

    const points = pointsOf(linePath(container));
    expect(points).toHaveLength(30);
    expect(points.every((point, index) => index === 0 || point.x > points[index - 1].x)).toBe(true);
  });

  it("puts a bigger total higher up the axis", () => {
    // SVG y grows downward, so a rising count has to produce falling y. An
    // inverted axis is the one geometry bug the data tests cannot see.
    const { container } = render(<ReviewChart snapshots={climbing(900, 3)} cardSnapshots={[]} endDate={END} />);

    const points = pointsOf(linePath(container));
    expect(points[29].y).toBeLessThan(points[0].y);
  });

  it("breaks the line at a missing day and bridges it with a dashed path", () => {
    const partial = climbing(900, 2).filter((snapshot) => snapshot.date !== "2026-08-20");
    const { container } = render(<ReviewChart snapshots={partial} cardSnapshots={[]} endDate={END} />);

    // Two separate subpaths mean two M commands in one `d`.
    expect(linePath(container).match(/M/g)).toHaveLength(2);
    expect(container.querySelector(".chart-bridge")).toBeInTheDocument();
    expect(container.querySelector(".chart-blank")).toBeInTheDocument();
  });

  it("counts the days it never received", () => {
    const partial = climbing(900, 2).filter(
      (snapshot) => snapshot.date !== "2026-08-20" && snapshot.date !== "2026-08-21",
    );
    render(<ReviewChart snapshots={partial} cardSnapshots={[]} endDate={END} />);

    expect(screen.getByText("Дней без данных").nextElementSibling).toHaveTextContent("2");
  });

  it("marks a month that lost reviews as negative", () => {
    const falling = climbing(1000, -4);
    render(<ReviewChart snapshots={falling} cardSnapshots={[]} endDate={END} />);

    expect(screen.getByText("За 30 дней").nextElementSibling).toHaveTextContent("−116");
  });

  it("says so plainly when there is nothing to draw", () => {
    render(<ReviewChart snapshots={[]} cardSnapshots={[]} endDate={END} />);

    expect(screen.getByText(/снимков по этому товару нет/)).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("switches to the whole склейка when asked", () => {
    const { container } = render(
      <ReviewChart snapshots={climbing(900, 1)} cardSnapshots={climbing(3400, 1)} endDate={END} />,
    );
    const byArticle = linePath(container);

    fireEvent.click(screen.getByRole("button", { name: "По карточке" }));

    expect(screen.getByText("Всего сейчас").nextElementSibling).toHaveTextContent("3 429");
    expect(linePath(container)).not.toBe("");
    expect(byArticle).not.toBe("");
  });

  it("offers no scope switch for a product outside a склейка", () => {
    render(<ReviewChart snapshots={climbing(900, 1)} cardSnapshots={[]} endDate={END} />);

    expect(screen.queryByRole("button", { name: "По карточке" })).not.toBeInTheDocument();
  });

  it("reports the day under the cursor", () => {
    const { container } = render(<ReviewChart snapshots={climbing(900, 2)} cardSnapshots={[]} endDate={END} />);
    expect(container.querySelector(".chart-tip")).not.toBeInTheDocument();

    // React derives onMouseEnter from a delegated mouseover, so a direct
    // mouseenter — which does not bubble — would never reach the handler.
    const hotspots = container.querySelectorAll(".chart-hotspot");
    fireEvent.mouseOver(hotspots[hotspots.length - 1]);

    // The date also sits on the axis, so read it out of the tooltip itself.
    const tip = container.querySelector(".chart-tip");
    expect(tip).toHaveTextContent("25 авг");
    expect(tip).toHaveTextContent("за день +2");
  });

  it("names a day the sync never delivered", () => {
    const partial = climbing(900, 2).filter((snapshot) => snapshot.date !== "2026-08-20");
    const { container } = render(<ReviewChart snapshots={partial} cardSnapshots={[]} endDate={END} />);

    const hotspots = container.querySelectorAll(".chart-hotspot");
    fireEvent.mouseOver(hotspots[24]);

    const tip = container.querySelector(".chart-tip");
    expect(tip).toHaveTextContent("Нет данных");
    expect(tip).toHaveTextContent("синхронизация не отработала");
  });
});
