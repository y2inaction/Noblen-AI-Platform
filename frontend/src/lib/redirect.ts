/**
 * Where to go after sign-in: a path on this site, never another origin.
 *
 * Prefix checks are not enough: browsers treat "\" like "/" and drop tabs and
 * newlines, so "/\evil.com" or "/%09/evil.com" would leave the site. The value is
 * resolved as a URL and accepted only when its origin is this one.
 */
export function safeNext(value: string | null, origin: string, fallback = "/dashboard"): string {
  if (!value || !value.startsWith("/")) return fallback;
  try {
    const url = new URL(value, origin);
    if (url.origin !== origin) return fallback;
    return url.pathname + url.search + url.hash;
  } catch {
    return fallback;
  }
}
