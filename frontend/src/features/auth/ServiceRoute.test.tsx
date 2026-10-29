import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ServiceRoute } from "./ServiceRoute";

let services: string[] = [];

vi.mock("./AuthContext", () => ({
  useAuth: () => ({ user: { id: "1", username: "lera", is_admin: false, services } }),
}));
vi.mock("../../pages/ServiceDeniedPage", () => ({ ServiceDeniedPage: () => <div>нет доступа</div> }));

function open(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<ServiceRoute />}>
          <Route path="/automations/wb-reviews" element={<div>отзывы</div>} />
          <Route path="/automations/fin-reports" element={<div>финансы</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("ServiceRoute", () => {
  it("opens a service that was granted", () => {
    services = ["wb-reviews"];
    open("/automations/wb-reviews");
    expect(screen.getByText("отзывы")).toBeInTheDocument();
  });

  it("shows the no-access page for a service that was not granted, even by a direct link", () => {
    services = ["wb-reviews"];
    open("/automations/fin-reports");
    expect(screen.getByText("нет доступа")).toBeInTheDocument();
    expect(screen.queryByText("финансы")).toBeNull();
  });
});
