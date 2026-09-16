import { randomUUID } from 'crypto';
import { resolutionCandidates } from './actname';
import type {
  EvaluateRequest,
  EvaluateResult,
  GovernanceTier,
  PolicyBundlePayload,
  PdpState,
  SignedPolicyBundle,
  ToolPolicy,
} from './types';

const TIER_RANK: Record<GovernanceTier, number> = {
  velocity: 1,
  efficiency: 2,
  control: 3,
};

function maxTier(a: GovernanceTier, b: GovernanceTier): GovernanceTier {
  return TIER_RANK[a] >= TIER_RANK[b] ? a : b;
}

export function resolveToolPolicy(
  payload: PolicyBundlePayload,
  toolName: string
): ToolPolicy | null {
  // Exact first, then the canonical dotted form. Order matters for safety, not style: an
  // act that resolves today resolves to the same entry after this change, so
  // canonicalization can only reach entries that were previously unreachable.
  for (const candidate of resolutionCandidates(toolName)) {
    if (payload.tools[candidate]) {
      return payload.tools[candidate];
    }
  }

  // DEPRECATED fallback: a default policy for acts the bundle does not list, keyed by a
  // `{domain}.{read|write}` group parsed out of the act name. It only ever works for acts
  // *named* in dotted form, which is not the form hosts send, so in practice it resolves
  // nothing. Retained unchanged for bundles that relied on it; author acts explicitly
  // instead. Do not extend this to canonicalized names — that would grant a policy nobody
  // wrote for acts nobody listed.
  const [domain, action] = toolName.split('.');
  if (!domain || !action) return null;

  const isRead =
    action.startsWith('get_') ||
    action.startsWith('list_') ||
    action === 'search';
  const categoryKey = `${domain}.${isRead ? 'read' : 'write'}`;
  return payload.tool_categories?.[categoryKey] ?? null;
}

/**
 * Evaluate a tool request against a verified policy bundle payload.
 * Most-restrictive-wins tier resolution.
 */
export function evaluateAgainstBundle(
  bundle: SignedPolicyBundle,
  request: EvaluateRequest,
  pdpState: PdpState = 'normal'
): EvaluateResult {
  const correlation_id = request.correlation_id || randomUUID();
  const payload = bundle.payload;

  if (pdpState === 'fail_closed') {
    return {
      decision: 'deny',
      tier: 'control',
      requires_approval: true,
      reasons: ['pdp_fail_closed'],
      bundle_version: payload.version,
      correlation_id,
      pdp_state: pdpState,
      category: null,
    };
  }

  const toolPolicy = resolveToolPolicy(payload, request.tool_name);
  if (!toolPolicy) {
    return {
      decision: 'deny',
      tier: 'control',
      requires_approval: true,
      reasons: ['unknown_tool', `tool:${request.tool_name}`],
      bundle_version: payload.version,
      correlation_id,
      pdp_state: pdpState,
      category: null,
    };
  }

  if (!toolPolicy.granted) {
    return {
      decision: 'deny',
      tier: toolPolicy.tier,
      requires_approval: true,
      reasons: ['not_granted', `tool:${request.tool_name}`],
      bundle_version: payload.version,
      correlation_id,
      pdp_state: pdpState,
      category: toolPolicy.category || null,
    };
  }

  const tier = maxTier(payload.default_tier, toolPolicy.tier);
  const requires_approval =
    toolPolicy.requires_approval || tier === 'control';

  const reasons: string[] = [`tier:${tier}`];
  if (pdpState === 'grace') reasons.push('grace_period');
  if (pdpState === 'cached') reasons.push('using_cached_bundle');
  if (requires_approval) reasons.push('requires_approval');

  return {
    decision: requires_approval ? 'require_approval' : 'allow',
    tier,
    requires_approval,
    reasons,
    bundle_version: payload.version,
    correlation_id,
    pdp_state: pdpState,
    // Declared on the entry or absent. Never inferred from the act's name: a host that
    // groups approvals must be told the grouping, not left to guess it from a spelling.
    category: toolPolicy.category || null,
  };
}
