import { describe, expect, it, vi } from "vitest";
import {
  CaptureError,
  DEFAULT_ALLOWED_PREFIXES,
  JPEG_QUALITY,
  MAX_UPLOAD_BYTES,
  captureActiveTab,
  dataUrlToBlob,
  fileNameFor,
  fitForUpload,
  isAllowedUrl,
  type TabsApi,
} from "../src/capture";

const CHAT = "https://seller.wildberries.ru/chat-with-clients?chatId=0b6c4d3e-1111-2222-3333-444455556666";
const PNG_DATA_URL = `data:image/png;base64,${Buffer.from("fake-png-bytes").toString("base64")}`;

describe("isAllowedUrl", () => {
  it("пускает чат с покупателем по префиксу по умолчанию", () => {
    expect(isAllowedUrl(CHAT, DEFAULT_ALLOWED_PREFIXES)).toBe(true);
    expect(isAllowedUrl("https://seller.wildberries.ru/chat-with-clients", DEFAULT_ALLOWED_PREFIXES)).toBe(true);
    expect(isAllowedUrl("https://seller.wildberries.ru/chat-with-clients/123#x", DEFAULT_ALLOWED_PREFIXES)).toBe(true);
  });

  it("не пускает другие страницы кабинета, чужие хосты и продолжение слова", () => {
    for (const url of [
      "https://seller.wildberries.ru/feedbacks",
      "https://seller.wildberries.ru/chat-with-clients-old",
      "https://seller.wildberries.ru.evil.example/chat-with-clients",
      "https://evil.example/https://seller.wildberries.ru/chat-with-clients",
      "http://seller.wildberries.ru/chat-with-clients",
      "https://user:pass@seller.wildberries.ru/chat-with-clients",
      "chrome://newtab/",
      "",
      undefined,
      "не адрес",
    ]) {
      expect(isAllowedUrl(url, DEFAULT_ALLOWED_PREFIXES), String(url)).toBe(false);
    }
  });

  it("берёт префиксы, пришедшие с сервера", () => {
    const prefixes = ["https://seller.wildberries.ru/chat-with-clients", "https://seller.wildberries.ru/support/chat"];
    expect(isAllowedUrl("https://seller.wildberries.ru/support/chat?id=1", prefixes)).toBe(true);
    expect(isAllowedUrl("https://seller.wildberries.ru/support/chats", prefixes)).toBe(false);
    expect(isAllowedUrl(CHAT, ["https://seller.wildberries.ru/support/chat"])).toBe(false);
    expect(isAllowedUrl(CHAT, [])).toBe(false);
    expect(isAllowedUrl(CHAT, ["мусор"])).toBe(false);
  });
});

function fakeTabs(tab: Partial<chrome.tabs.Tab> | undefined, capture: () => Promise<string> = async () => PNG_DATA_URL): TabsApi {
  return {
    query: vi.fn(async () => (tab ? [tab as chrome.tabs.Tab] : [])),
    captureVisibleTab: vi.fn(capture),
  };
}

describe("captureActiveTab", () => {
  it("снимает активную вкладку в PNG и собирает метаданные", async () => {
    const tabs = fakeTabs({ id: 7, windowId: 3, url: CHAT, width: 1440, height: 900 });
    const at = new Date("2026-10-23T09:00:00Z");
    const capture = await captureActiveTab(tabs, () => at, 2);
    expect(tabs.captureVisibleTab).toHaveBeenCalledWith(3, { format: "png" });
    expect(tabs.query).toHaveBeenCalledWith({ active: true, currentWindow: true });
    expect(capture.url).toBe(CHAT);
    expect(capture.capturedAt).toBe("2026-10-23T09:00:00.000Z");
    expect(capture.tzOffsetMin).toBe(at.getTimezoneOffset());
    expect([capture.viewportW, capture.viewportH, capture.dpr]).toEqual([1440, 900, 2]);
    expect(capture.blob.type).toBe("image/png");
    expect(await capture.blob.text()).toBe("fake-png-bytes");
    expect(capture.id).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("каждый захват получает свой id", async () => {
    const tabs = fakeTabs({ windowId: 1, url: CHAT });
    const [a, b] = await Promise.all([captureActiveTab(tabs), captureActiveTab(tabs)]);
    expect(a.id).not.toBe(b.id);
  });

  it("не чат: снимок есть, но адрес не проходит проверку", async () => {
    const capture = await captureActiveTab(fakeTabs({ windowId: 1, url: "https://seller.wildberries.ru/analytics" }));
    expect(isAllowedUrl(capture.url, DEFAULT_ALLOWED_PREFIXES)).toBe(false);
  });

  it("без активной вкладки и при отказе браузера — понятная ошибка", async () => {
    await expect(captureActiveTab(fakeTabs(undefined))).rejects.toBeInstanceOf(CaptureError);
    await expect(
      captureActiveTab(fakeTabs({ windowId: 1, url: CHAT }, async () => Promise.reject(new Error("Cannot access contents"))))
    ).rejects.toThrow(/Cannot access contents/);
  });
});

describe("dataUrlToBlob", () => {
  it("разбирает base64 и отвергает мусор", async () => {
    const blob = dataUrlToBlob(PNG_DATA_URL);
    expect(blob.type).toBe("image/png");
    expect(blob.size).toBe("fake-png-bytes".length);
    expect(() => dataUrlToBlob("не data url")).toThrow(CaptureError);
  });
});

describe("fitForUpload", () => {
  it("снимок до 6 МБ уходит как есть", async () => {
    const encoder = { toJpeg: vi.fn() };
    const blob = new Blob([new Uint8Array(MAX_UPLOAD_BYTES)], { type: "image/png" });
    expect(await fitForUpload(blob, encoder)).toBe(blob);
    expect(encoder.toJpeg).not.toHaveBeenCalled();
    expect(fileNameFor(blob)).toBe("tab.png");
  });

  it("больше 6 МБ — перекодирование в JPEG q=0.92", async () => {
    const jpeg = new Blob([new Uint8Array(1024)], { type: "image/jpeg" });
    const encoder = { toJpeg: vi.fn(async () => jpeg) };
    const big = new Blob([new Uint8Array(MAX_UPLOAD_BYTES + 1)], { type: "image/png" });
    const result = await fitForUpload(big, encoder);
    expect(encoder.toJpeg).toHaveBeenCalledWith(big, JPEG_QUALITY);
    expect(JPEG_QUALITY).toBe(0.92);
    expect(result).toBe(jpeg);
    expect(fileNameFor(result)).toBe("tab.jpg");
  });
});
