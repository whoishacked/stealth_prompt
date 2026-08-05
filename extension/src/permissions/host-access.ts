/**
 * Optional per-origin host access.
 *
 * `chrome.permissions.request()` must be reached directly from a user gesture.
 * Keep this helper free of any preceding asynchronous lookup: the Side Panel
 * already knows the reviewed target origin before a Select button is pressed.
 */

export interface HostAccessResult {
  granted: boolean;
  pattern: string;
  message: string;
}

type RequestPermission = (permissions: chrome.permissions.Permissions) => Promise<boolean>;

/** Convert a reviewed HTTP(S) origin to one exact host-permission pattern. */
export function hostPermissionPattern(origin: string): string {
  let url: URL;
  try {
    url = new URL(origin);
  } catch {
    throw new Error('The selected target origin is not a valid URL.');
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error('Only HTTP and HTTPS pages can be selected.');
  }
  if (url.origin !== origin) {
    throw new Error('The target must be an origin without a path or query.');
  }
  return `${url.origin}/*`;
}

/**
 * Request access immediately.
 *
 * Do not add an awaited `permissions.contains()` before this call. That async
 * gap can consume Chrome's transient user activation and make the real
 * permission prompt fail even though the operator just clicked Select.
 */
export async function requestHostAccess(
  origin: string,
  request: RequestPermission = (permissions) => chrome.permissions.request(permissions),
): Promise<HostAccessResult> {
  const pattern = hostPermissionPattern(origin);
  try {
    const granted = await request({ origins: [pattern] });
    return {
      granted,
      pattern,
      message: granted
        ? ''
        : `Chrome did not grant access to ${origin}. Allow this site and try again.`,
    };
  } catch (error) {
    const raw = (error as Error)?.message ?? String(error ?? 'permission request failed');
    const flat = raw.replace(/\s+/g, ' ').trim();
    return {
      granted: false,
      pattern,
      message: flat
        ? `Could not request access to ${origin}: ${flat}`
        : `Could not request access to ${origin}.`,
    };
  }
}
