import { afterEach, describe, expect, it, vi } from "vitest";

import { adminEntryUrl } from "./entry";

function onHost(hostname: string) {
  vi.stubGlobal("location", { hostname, protocol: "https:" });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("adminEntryUrl", () => {
  it("stays in the app when already on the admin host", () => {
    onHost("admin.marketplace-auto.ru");
    expect(adminEntryUrl("/admin/users")).toBe("/admin/users");
  });

  it("sends the browser to the admin host from the main domain", () => {
    onHost("marketplace-auto.ru");
    expect(adminEntryUrl("/admin/users")).toBe("https://admin.marketplace-auto.ru/admin/users");
  });

  it("does not build admin.www.<domain>", () => {
    onHost("www.marketplace-auto.ru");
    expect(adminEntryUrl("/admin/users")).toBe("https://admin.marketplace-auto.ru/admin/users");
  });

  it("keeps local development in one origin", () => {
    onHost("localhost");
    expect(adminEntryUrl("/admin/users")).toBe("/admin/users");
  });
});
