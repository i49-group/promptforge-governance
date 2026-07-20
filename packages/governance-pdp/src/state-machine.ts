import type { PdpState, PolicyBundlePayload } from './types';

/**
 * Derive PDP operational state from bundle expiry + grace (P3).
 * `refreshFailed` upgrades normal→cached when still before expires_at.
 */
export function derivePdpState(
  payload: PolicyBundlePayload | null,
  options: { now?: number; refreshFailed?: boolean } = {}
): PdpState {
  if (!payload) return 'fail_closed';

  const now = options.now ?? Date.now();
  const expires = Date.parse(payload.expires_at);
  if (Number.isNaN(expires)) return 'fail_closed';

  if (now < expires) {
    return options.refreshFailed ? 'cached' : 'normal';
  }

  if (now < expires + payload.grace_ms) {
    return 'grace';
  }

  return 'fail_closed';
}
