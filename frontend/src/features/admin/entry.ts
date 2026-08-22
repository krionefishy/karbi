const ADMIN_PREFIX = "admin.";
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", ""]);

/** The admin section has its own host: nginx answers 403 to /api/v1/admin/ on
 * the main domain, so the two interfaces never share a page. Locally there is
 * one origin for both, and the section behaves like an ordinary route. */
export function isAdminHost(): boolean {
  return window.location.hostname.startsWith(ADMIN_PREFIX);
}

/** Entry point to the admin section from wherever you are now. */
export function adminEntryUrl(path: string): string {
  const { hostname, protocol } = window.location;
  if (LOCAL_HOSTS.has(hostname) || isAdminHost()) return path;
  return `${protocol}//${ADMIN_PREFIX}${hostname.replace(/^www\./, "")}${path}`;
}

/** The way back to sellers and automations. */
export function workspaceUrl(path: string): string {
  const { hostname, protocol } = window.location;
  if (!isAdminHost()) return path;
  return `${protocol}//${hostname.slice(ADMIN_PREFIX.length)}${path}`;
}

/** Where a session lands when no particular page was asked for. On the admin
 * host that is the admin section, otherwise the workspace. */
export function homeRoute(): string {
  return isAdminHost() ? "/admin/users" : "/automations";
}
