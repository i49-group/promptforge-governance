import { evaluateAgainstBundle } from './evaluate';
import { derivePdpState } from './state-machine';
import type {
  BundleFetchOutcome,
  GovernanceContentPack,
  GovernanceEnvironment,
  GovernancePdp,
  PdpOptions,
  PdpState,
  SignedPolicyBundle,
  EvaluateRequest,
  EvaluateResult,
} from './types';
import { verifyPayloadHs256 } from './verify';

/** A non-OK HTTP answer, kept apart from network errors so a 404 can be told from an outage. */
export class GovernanceFetchError extends Error {
  constructor(
    readonly status: number,
    message: string
  ) {
    super(message);
    this.name = 'GovernanceFetchError';
  }
}

interface StandardEnvelope<T> {
  success: boolean;
  data?: T;
  error?: string;
}

export function createGovernancePdp(options: PdpOptions): GovernancePdp {
  const environment: GovernanceEnvironment =
    options.environment === 'staging' ? 'staging' : 'production';
  const timeoutMs = options.fetchTimeoutMs ?? 500;
  const fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  const now = options.now ?? (() => Date.now());

  let pack: GovernanceContentPack | null = null;
  let bundle: SignedPolicyBundle | null = null;
  let lastRefreshFailed = false;
  let lastBundleFetch: BundleFetchOutcome | null = null;

  function currentState(): PdpState {
    return derivePdpState(bundle?.payload ?? null, {
      now: now(),
      refreshFailed: lastRefreshFailed,
    });
  }

  async function fetchJson<T>(path: string): Promise<T> {
    const base = options.baseUrl.replace(/\/$/, '');
    const url = `${base}${path}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const res = await fetchImpl(url, {
        method: 'GET',
        headers: {
          ...options.headers,
          Authorization: `Bearer ${options.token}`,
          Accept: 'application/json',
        },
        signal: controller.signal,
      });

      if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new GovernanceFetchError(res.status, `HTTP ${res.status}: ${text || res.statusText}`);
      }

      const json = (await res.json()) as StandardEnvelope<T>;
      if (!json.success || json.data === undefined) {
        throw new Error(json.error || 'Unsuccessful governance response');
      }
      return json.data;
    } finally {
      clearTimeout(timer);
    }
  }

  return {
    async refresh() {
      const agentKey = options.agentKey.toLowerCase();
      const qs = `environment=${environment}`;

      // The pack is Talk and the bundle is Act: a missing or failed pack never stops the bundle.
      const [packResult, bundleResult] = await Promise.allSettled([
        fetchJson<GovernanceContentPack>(
          `/api/governance/packs/${encodeURIComponent(agentKey)}?${qs}`
        ),
        fetchJson<SignedPolicyBundle>(
          `/api/governance/bundles/${encodeURIComponent(agentKey)}?${qs}`
        ),
      ]);
      if (packResult.status === 'fulfilled') pack = packResult.value;

      try {
        if (bundleResult.status === 'rejected') {
          const err = bundleResult.reason;
          lastBundleFetch =
            err instanceof GovernanceFetchError && err.status === 404 ? 'not_found' : 'failed';
          throw err;
        }

        const nextBundle = bundleResult.value;
        if (nextBundle.alg !== 'HS256') {
          lastBundleFetch = 'unverified';
          throw new Error(`Unsupported bundle alg: ${nextBundle.alg}`);
        }
        if (
          !verifyPayloadHs256(
            nextBundle.payload,
            nextBundle.signature,
            options.verifyKey
          )
        ) {
          lastBundleFetch = 'unverified';
          throw new Error('Bundle signature verification failed');
        }

        bundle = nextBundle;
        lastBundleFetch = 'ok';
        lastRefreshFailed = false;

        return {
          packVersion: pack?.version || 'unknown',
          bundleVersion: bundle.payload.version,
          state: currentState(),
        };
      } catch (err) {
        lastRefreshFailed = true;
        // Keep last-known bundle; state machine handles grace/fail-closed
        if (!bundle) {
          throw err;
        }
        return {
          packVersion: pack?.version || 'unknown',
          bundleVersion: bundle.payload.version,
          state: currentState(),
        };
      }
    },

    getContentPack() {
      return pack;
    },

    getBundle() {
      return bundle;
    },

    getLastBundleFetch() {
      return lastBundleFetch;
    },

    getBundleMeta() {
      if (!bundle) return null;
      return {
        version: bundle.payload.version,
        expiresAt: bundle.payload.expires_at,
        state: currentState(),
      };
    },

    evaluate(req: EvaluateRequest): EvaluateResult {
      const state = currentState();
      if (!bundle) {
        return {
          decision: 'deny',
          tier: 'control',
          requires_approval: true,
          reasons: ['no_bundle_loaded', 'pdp_fail_closed'],
          bundle_version: 'none',
          correlation_id: req.correlation_id || 'no-bundle',
          pdp_state: 'fail_closed',
          // No bundle means no entry, so no group. A refusal must never be widened into a
          // group approval.
          category: null,
        };
      }
      return evaluateAgainstBundle(
        bundle,
        { ...req, agent_key: req.agent_key || options.agentKey },
        state
      );
    },
  };
}
