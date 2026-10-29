import { beforeEach, describe, expect, it } from "vitest";

import { clearImageCache, forgetImage, presignedExpiry, stableImageUrl } from "./images";

const NOW = Date.parse("2026-10-23T09:00:00Z");
const signed = (date: string, salt: string) =>
  `https://s3.example.ru/bucket/t.webp?X-Amz-Date=${date}&X-Amz-Expires=300&X-Amz-Signature=${salt}`;

beforeEach(() => clearImageCache());

describe("presigned-ссылки миниатюр", () => {
  it("reads expiry from SigV4 and SigV2 urls, falls back to four minutes", () => {
    expect(presignedExpiry(signed("20261023T090000Z", "a"), NOW)).toBe(NOW + 300_000);
    expect(presignedExpiry("https://s3/x?Expires=1792000000", NOW)).toBe(1_792_000_000_000);
    expect(presignedExpiry("/local/thumb.webp", NOW)).toBe(NOW + 240_000);
  });

  it("keeps the first url for an id until it is about to expire", () => {
    const first = signed("20261023T090000Z", "a");
    expect(stableImageUrl("thumb:1", first, { now: NOW })).toBe(first);
    // Опрос через 5 с принёс новую подпись — картинка не должна перекачиваться.
    expect(stableImageUrl("thumb:1", signed("20261023T090005Z", "b"), { now: NOW + 5_000 })).toBe(first);
    // За 30 с до истечения берём свежую.
    const fresh = signed("20261023T090431Z", "c");
    expect(stableImageUrl("thumb:1", fresh, { now: NOW + 271_000 })).toBe(fresh);
  });

  it("uses an explicit expires_at when the server sends it", () => {
    stableImageUrl("thumb:2", "https://s3/a", { now: NOW, expiresAt: "2026-10-23T09:01:00Z" });
    expect(stableImageUrl("thumb:2", "https://s3/b", { now: NOW + 20_000 })).toBe("https://s3/a");
    expect(stableImageUrl("thumb:2", "https://s3/c", { now: NOW + 31_000 })).toBe("https://s3/c");
  });

  it("forgets the url on load error and when the image is gone", () => {
    stableImageUrl("thumb:3", "https://s3/a", { now: NOW });
    forgetImage("thumb:3");
    expect(stableImageUrl("thumb:3", "https://s3/b", { now: NOW + 1_000 })).toBe("https://s3/b");
    expect(stableImageUrl("thumb:3", null, { now: NOW + 2_000 })).toBeNull();
    expect(stableImageUrl("thumb:3", "https://s3/c", { now: NOW + 3_000 })).toBe("https://s3/c");
  });
});
