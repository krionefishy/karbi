import { Search } from "lucide-react";
import { useState } from "react";

import { FbsPenaltiesTable } from "./FbsPenaltiesTable";
import type { Lookup } from "../features/fbsPenalties/types";

interface Props {
  result: Lookup | null;
  pending: boolean;
  onLookup: (text: string) => void;
}

/** Вставила номера из своей таблицы — получила склад и поставку по каждому. */
export function FbsPenaltiesLookup({ result, pending, onLookup }: Props) {
  const [draft, setDraft] = useState("");
  return (
    <>
      <form
        className="stocks-add penalties-lookup"
        onSubmit={(event) => {
          event.preventDefault();
          if (draft.trim()) onLookup(draft);
        }}
      >
        <textarea
          aria-label="Номера для проверки"
          placeholder="Стикеры МП, номера сборочных заданий или srid — по одному в строке, через запятую или пробел."
          value={draft}
          rows={3}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button className="primary-button" type="submit" disabled={pending || !draft.trim()}>
          <Search size={15} />
          {pending ? "Ищем…" : "Найти"}
        </button>
      </form>
      {result && (
        <>
          {result.missing.length > 0 && (
            <div className="checklist-notice">
              Не найдено: {result.missing.map((miss) => `${miss.key} — ${miss.reason}`).join("; ")}
            </div>
          )}
          <FbsPenaltiesTable rows={result.rows} empty="По этим номерам ничего не нашлось." />
        </>
      )}
    </>
  );
}
