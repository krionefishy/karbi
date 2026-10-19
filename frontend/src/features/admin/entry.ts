const ADMIN_PREFIX = "admin.";
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", ""]);

export function isAdminHost(): boolean {
  return window.location.hostname.startsWith(ADMIN_PREFIX);
}

export function adminEntryUrl(path: string): string {
  const { hostname, protocol } = window.location;
  if (LOCAL_HOSTS.has(hostname) || isAdminHost()) return path;
  return `${protocol}//${ADMIN_PREFIX}${hostname.replace(/^www\./, "")}${path}`;
}

export function workspaceUrl(path: string): string {
  const { hostname, protocol } = window.location;
  if (!isAdminHost()) return path;
  return `${protocol}//${hostname.slice(ADMIN_PREFIX.length)}${path}`;
}

export function homeRoute(): string {
  return isAdminHost() ? "/admin/users" : "/automations";
}
