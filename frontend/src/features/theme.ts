import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";
type ThemeChoice = Theme | "system";

const STORAGE_KEY = "marketplace-auto.theme";

function readChoice(): ThemeChoice {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

function systemTheme(): Theme {
  return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function apply(choice: ThemeChoice) {
  const root = document.documentElement;
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
}

/** Тема хранится в браузере: без выбора интерфейс следует за системой,
 * а `data-theme` на <html> переключает токены — стили сами ничего не знают. */
export function useTheme(): { theme: Theme; toggle: () => void } {
  const [choice, setChoice] = useState<ThemeChoice>(readChoice);
  useEffect(() => apply(choice), [choice]);
  const theme = choice === "system" ? systemTheme() : choice;
  const toggle = useCallback(() => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* приватный режим: тема живёт до перезагрузки */
    }
    setChoice(next);
  }, [theme]);
  return { theme, toggle };
}
