import { randomUUID } from 'crypto';
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
  if (payload.tools[toolName]) {
    return payload.tools[toolName];
  }

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
 * Most-restrictive-wins tier resolution (aligned with BC promptforge-governance).
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
  };
}
