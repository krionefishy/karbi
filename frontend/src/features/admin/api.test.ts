import { afterEach, describe, expect, it, vi } from "vitest";

import { createEmployee, resetEmployeePassword, setEmployeeServices, updateEmployee } from "./api";

function stubJson(status = 200) {
  const request = vi
    .fn()
    .mockResolvedValue(new Response("{}", { status, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", request);
  return request;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("employee admin api", () => {
  it("never sends a password when creating an employee", async () => {
    const request = stubJson(201);

    await createEmployee({ username: "ivanov", is_admin: false });

    const body = String(request.mock.calls[0][1].body);
    expect(JSON.parse(body)).toEqual({ username: "ivanov", is_admin: false });
    expect(body).not.toContain("password");
  });

  it("sends only the field being changed", async () => {
    const request = stubJson();

    await updateEmployee("user-1", { is_active: false });

    expect(request.mock.calls[0][0]).toBe("/api/v1/admin/users/user-1");
    expect(JSON.parse(String(request.mock.calls[0][1].body))).toEqual({ is_active: false });
  });

  it("resets a password with an empty POST", async () => {
    const request = stubJson();

    await resetEmployeePassword("user-1");

    expect(request.mock.calls[0][0]).toBe("/api/v1/admin/users/user-1/password");
    expect(request.mock.calls[0][1].method).toBe("POST");
    expect(request.mock.calls[0][1].body).toBeUndefined();
  });

  it("replaces the whole set of services with a PUT", async () => {
    const request = stubJson();

    await setEmployeeServices("user-1", ["wb-reviews", "fin-reports"]);

    expect(request.mock.calls[0][0]).toBe("/api/v1/admin/users/user-1/services");
    expect(request.mock.calls[0][1].method).toBe("PUT");
    expect(JSON.parse(String(request.mock.calls[0][1].body))).toEqual({ service_ids: ["wb-reviews", "fin-reports"] });
  });
});
