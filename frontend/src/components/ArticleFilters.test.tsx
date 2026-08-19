import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EMPTY_FILTERS } from "../features/reviews/filters";
import { ArticleFilters } from "./ArticleFilters";

const subjects = [
  { name: "Перфораторы", count: 12 },
  { name: "Пылесосы", count: 4 },
  { name: "Без предмета", count: 1 },
];

describe("ArticleFilters", () => {
  it("reports the search text as it is typed", () => {
    const onChange = vi.fn();
    render(
      <ArticleFilters filters={EMPTY_FILTERS} subjects={subjects} shown={17} total={17} onChange={onChange} />,
    );

    fireEvent.change(screen.getByLabelText("Поиск по артикулу и предмету"), { target: { value: "5268" } });

    expect(onChange).toHaveBeenCalledWith({ query: "5268", subjects: [] });
  });

  it("adds and removes subjects without losing the others", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <ArticleFilters filters={EMPTY_FILTERS} subjects={subjects} shown={17} total={17} onChange={onChange} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Все предметы/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Перфораторы/ }));
    expect(onChange).toHaveBeenCalledWith({ query: "", subjects: ["Перфораторы"] });

    rerender(
      <ArticleFilters
        filters={{ query: "", subjects: ["Перфораторы", "Пылесосы"] }}
        subjects={subjects}
        shown={16}
        total={17}
        onChange={onChange}
      />,
    );
    // The menu stays open while subjects are picked, so the trigger — now
    // reading "Предметов: 2" — must not be clicked again here.
    expect(screen.getByRole("button", { name: /Предметов: 2/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /Пылесосы/ }));
    expect(onChange).toHaveBeenLastCalledWith({ query: "", subjects: ["Перфораторы"] });
  });

  it("counts the goods only while a filter narrows them", () => {
    const { rerender } = render(
      <ArticleFilters filters={EMPTY_FILTERS} subjects={subjects} shown={17} total={17} onChange={vi.fn()} />,
    );
    expect(screen.getByText(/Всего товаров:/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Сбросить/ })).not.toBeInTheDocument();

    rerender(
      <ArticleFilters
        filters={{ query: "перф", subjects: [] }}
        subjects={subjects}
        shown={12}
        total={17}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText(/Показано/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Сбросить/ })).toBeInTheDocument();
  });

  it("clears everything at once", () => {
    const onChange = vi.fn();
    render(
      <ArticleFilters
        filters={{ query: "перф", subjects: ["Пылесосы"] }}
        subjects={subjects}
        shown={0}
        total={17}
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Сбросить/ }));

    expect(onChange).toHaveBeenCalledWith({ query: "", subjects: [] });
  });

  it("shows the count beside every subject", () => {
    render(
      <ArticleFilters filters={EMPTY_FILTERS} subjects={subjects} shown={17} total={17} onChange={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Все предметы/ }));

    expect(screen.getByRole("checkbox", { name: /Перфораторы 12/ })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Без предмета 1/ })).toBeInTheDocument();
  });
});
