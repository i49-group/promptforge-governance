import { evaluateAgainstBundle } from './evaluate';
import { derivePdpState } from './state-machine';
import type {
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
          Authorization: `Bearer ${options.token}`,
          Accept: 'application/json',
        },
        signal: controller.signal,
      });

      if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new Error(`HTTP ${res.status}: ${text || res.statusText}`);
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

      try {
        const [nextPack, nextBundle] = await Promise.all([
          fetchJson<GovernanceContentPack>(
            `/api/governance/packs/${encodeURIComponent(agentKey)}?${qs}`
          ),
          fetchJson<SignedPolicyBundle>(
            `/api/governance/bundles/${encodeURIComponent(agentKey)}?${qs}`
          ),
        ]);

        if (nextBundle.alg !== 'HS256') {
          throw new Error(`Unsupported bundle alg: ${nextBundle.alg}`);
        }

        if (
          !verifyPayloadHs256(
            nextBundle.payload,
            nextBundle.signature,
            options.verifyKey
          )
        ) {
          throw new Error('Bundle signature verification failed');
        }

        pack = nextPack;
        bundle = nextBundle;
        lastRefreshFailed = false;

        const state = currentState();
        return {
          packVersion: pack.version,
          bundleVersion: bundle.payload.version,
          state,
        };
      } catch (err) {
        lastRefreshFailed = true;
        // Keep last-known pack/bundle; state machine handles grace/fail-closed
        if (!bundle) {
          throw err;
        }
        const state = currentState();
        return {
          packVersion: pack?.version || 'unknown',
          bundleVersion: bundle.payload.version,
          state,
        };
      }
    },

    getContentPack() {
      return pack;
    },

    getBundle() {
      return bundle;
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
