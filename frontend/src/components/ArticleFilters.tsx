import { Check, ChevronDown, Search, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { ArticleFilters as Filters, Subject } from "../features/reviews/filters";
import { hasActiveFilters } from "../features/reviews/filters";

interface ArticleFiltersProps {
  filters: Filters;
  subjects: Subject[];
  shown: number;
  total: number;
  onChange: (filters: Filters) => void;
}

export function ArticleFilters({ filters, subjects, shown, total, onChange }: ArticleFiltersProps) {
  const [open, setOpen] = useState(false);
  const menu = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!menu.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const toggleSubject = (name: string) => {
    const subjects = filters.subjects.includes(name)
      ? filters.subjects.filter((item) => item !== name)
      : [...filters.subjects, name];
    onChange({ ...filters, subjects });
  };

  const active = hasActiveFilters(filters);
  const label =
    filters.subjects.length === 0
      ? "Все предметы"
      : filters.subjects.length === 1
        ? filters.subjects[0]
        : `Предметов: ${filters.subjects.length}`;

  return (
    <div className="article-filters">
      <label className="filter-search">
        <Search size={15} aria-hidden="true" />
        <input
          type="search"
          value={filters.query}
          placeholder="Артикул WB, артикул продавца или предмет"
          aria-label="Поиск по артикулу и предмету"
          onChange={(event) => onChange({ ...filters, query: event.target.value })}
        />
      </label>

      <div className="filter-subjects" ref={menu}>
        <button
          type="button"
          className={`filter-trigger${filters.subjects.length ? " filter-trigger-active" : ""}`}
          aria-expanded={open}
          aria-haspopup="true"
          onClick={() => setOpen((value) => !value)}
        >
          {label}
          <ChevronDown size={14} aria-hidden="true" />
        </button>
        {open && (
          <div className="filter-menu" role="group" aria-label="Предметы карточек">
            {subjects.length === 0 ? (
              <p className="filter-menu-empty">Предметы неизвестны</p>
            ) : (
              subjects.map((subject) => {
                const checked = filters.subjects.includes(subject.name);
                return (
                  <button
                    type="button"
                    className={`filter-option${checked ? " filter-option-checked" : ""}`}
                    key={subject.name}
                    role="checkbox"
                    aria-checked={checked}
                    onClick={() => toggleSubject(subject.name)}
                  >
                    <i aria-hidden="true">{checked && <Check size={11} strokeWidth={3} />}</i>
                    <span>{subject.name}</span>
                    <b>{subject.count}</b>
                  </button>
                );
              })
            )}
          </div>
        )}
      </div>

      <span className="filter-count">
        {active ? (
          <>
            Показано <b>{shown}</b> из {total}
          </>
        ) : (
          <>
            Всего товаров: <b>{total}</b>
          </>
        )}
      </span>

      {active && (
        <button type="button" className="filter-reset" onClick={() => onChange({ query: "", subjects: [] })}>
          <X size={13} aria-hidden="true" /> Сбросить
        </button>
      )}
    </div>
  );
}
