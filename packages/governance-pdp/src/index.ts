/**
 * @promptforge/governance-pdp
 *
 * Local Policy Decision Point for PromptForge governance bundles.
 * MCP/REST are for discovery & distribution; evaluate stays in-process.
 */

export { createGovernancePdp } from './pdp';
export { evaluateAgainstBundle, resolveToolPolicy } from './evaluate';
export { derivePdpState } from './state-machine';
export { canonicalJson } from './canonical';
export { signPayloadHs256, verifyPayloadHs256 } from './verify';

export type {
  ContextAssemblyType,
  EvaluateDecision,
  EvaluateRequest,
  EvaluateResult,
  GovernanceContentPack,
  GovernanceContextRef,
  GovernanceEnvironment,
  GovernancePdp,
  GovernancePromptRef,
  GovernanceTier,
  PdpOptions,
  PdpState,
  PolicyBundlePayload,
  SignedPolicyBundle,
  ToolPolicy,
} from './types';
