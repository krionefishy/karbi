import { describe, expect, it } from "vitest";

import { ApiError } from "../../api/http";
import { apiErrorText, isFileRejected } from "./errors";

describe("тексты ошибок API", () => {
  it("explains the new backend codes in plain Russian", () => {
    expect(apiErrorText(new ApiError(409, "Такое решение недоступно", "transition_not_allowed"))).toBe(
      "Это решение для скриншота уже недоступно: его разобрали раньше. Обновите очередь.",
    );
    expect(apiErrorText(new ApiError(422, "Курсор не подходит", "bad_cursor"))).toBe(
      "Список изменился, пока вы его листали. Загрузите его заново.",
    );
  });

  it("falls back to the server detail, then to the given text", () => {
    expect(apiErrorText(new ApiError(404, "Установка не найдена", "not_found"))).toBe("Установка не найдена");
    expect(apiErrorText(new ApiError(500, "", null), "Не удалось выпустить код")).toBe("Не удалось выпустить код");
    expect(apiErrorText(new TypeError("Failed to fetch"))).toBe("Нет связи с сервером");
  });

  it("does not offer a retry for files the server rejected", () => {
    expect(isFileRejected(new ApiError(413, "Большой", "file_too_large"))).toBe(true);
    expect(isFileRejected(new ApiError(422, "Ошибка", null))).toBe(true);
    expect(isFileRejected(new ApiError(429, "Часто", "rate_limited"))).toBe(false);
    expect(isFileRejected(new ApiError(503, "Хранилище", "storage_full"))).toBe(false);
    expect(isFileRejected(new Error("сеть"))).toBe(false);
  });
});
