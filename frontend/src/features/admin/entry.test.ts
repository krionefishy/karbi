import { afterEach, describe, expect, it, vi } from "vitest";

import { adminEntryUrl, homeRoute, isAdminHost, workspaceUrl } from "./entry";

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

describe("workspaceUrl", () => {
  it("leads back to the main domain from the admin host", () => {
    onHost("admin.marketplace-auto.ru");
    expect(workspaceUrl("/automations")).toBe("https://marketplace-auto.ru/automations");
  });

  it("stays in the app when already in the workspace", () => {
    onHost("marketplace-auto.ru");
    expect(workspaceUrl("/automations")).toBe("/automations");
  });
});

describe("homeRoute", () => {
  it("lands a session on the admin section when it opened the admin host", () => {
    onHost("admin.marketplace-auto.ru");
    expect(isAdminHost()).toBe(true);
    expect(homeRoute()).toBe("/admin/users");
  });

  it("lands a session on the workspace everywhere else", () => {
    onHost("marketplace-auto.ru");
    expect(homeRoute()).toBe("/automations");
  });
});
