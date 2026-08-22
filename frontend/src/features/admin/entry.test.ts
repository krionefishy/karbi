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
    onHost("admin.example.com");
    expect(adminEntryUrl("/admin/users")).toBe("/admin/users");
  });

  it("sends the browser to the admin host from the main domain", () => {
    onHost("example.com");
    expect(adminEntryUrl("/admin/users")).toBe("https://admin.example.com/admin/users");
  });

  it("does not build admin.www.<domain>", () => {
    onHost("www.example.com");
    expect(adminEntryUrl("/admin/users")).toBe("https://admin.example.com/admin/users");
  });

  it("keeps local development in one origin", () => {
    onHost("localhost");
    expect(adminEntryUrl("/admin/users")).toBe("/admin/users");
  });
});

describe("workspaceUrl", () => {
  it("leads back to the main domain from the admin host", () => {
    onHost("admin.example.com");
    expect(workspaceUrl("/automations")).toBe("https://example.com/automations");
  });

  it("stays in the app when already in the workspace", () => {
    onHost("example.com");
    expect(workspaceUrl("/automations")).toBe("/automations");
  });
});

describe("homeRoute", () => {
  it("lands a session on the admin section when it opened the admin host", () => {
    onHost("admin.example.com");
    expect(isAdminHost()).toBe(true);
    expect(homeRoute()).toBe("/admin/users");
  });

  it("lands a session on the workspace everywhere else", () => {
    onHost("example.com");
    expect(homeRoute()).toBe("/automations");
  });
});
