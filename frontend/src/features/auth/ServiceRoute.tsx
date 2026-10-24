import { Outlet, useMatch } from "react-router-dom";

import { ServiceDeniedPage } from "../../pages/ServiceDeniedPage";
import { useAuth } from "./AuthContext";

/** Страницы `/automations/<id>` открыты только тем, кому админ выдал сервис с таким id. */
export function ServiceRoute() {
  const { user } = useAuth();
  const service = useMatch("/automations/:service/*")?.params.service;
  if (service && !user?.services.includes(service)) return <ServiceDeniedPage />;
  return <Outlet />;
}
