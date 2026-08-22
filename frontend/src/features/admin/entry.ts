const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", ""]);

/** Where the admin section is reachable from the page you are on.
 *
 * The backend is shared, but nginx answers 403 to /api/v1/admin/ on the main
 * domain, so the section only works on admin.<domain>. Following a normal
 * in-app link from the main domain would open a page whose every request fails;
 * this sends the browser to the right host instead.
 */
export function adminEntryUrl(path: string): string {
  const { hostname, protocol } = window.location;
  if (LOCAL_HOSTS.has(hostname) || hostname.startsWith("admin.")) return path;
  return `${protocol}//admin.${hostname.replace(/^www\./, "")}${path}`;
}
