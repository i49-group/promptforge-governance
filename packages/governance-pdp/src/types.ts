export type GovernanceEnvironment = 'staging' | 'production';
export type GovernanceTier = 'velocity' | 'efficiency' | 'control';
export type ContextAssemblyType = 'soul' | 'principles' | 'operational' | 'knowledge';
export type EvaluateDecision = 'allow' | 'require_approval' | 'deny';
export type PdpState = 'normal' | 'cached' | 'grace' | 'fail_closed';

export interface GovernanceContextRef {
  id: string;
  name: string;
  context_type: ContextAssemblyType;
  version?: number;
  content: string;
  content_hash: string;
  tags: string[];
}

export interface GovernancePromptRef {
  id: string;
  name: string;
  content: string;
  content_hash: string;
  tags: string[];
}

export interface GovernanceContentPack {
  agent_key: string;
  environment: GovernanceEnvironment;
  version: string;
  etag: string;
  published_at: string;
  digital_worker_email?: string;
  source?: 'database';
  contexts: GovernanceContextRef[];
  prompts: GovernancePromptRef[];
  assembly: {
    order: ContextAssemblyType[];
  };
}

export interface ToolPolicy {
  tier: GovernanceTier;
  requires_approval: boolean;
  granted: boolean;
  /**
   * Optional approval group this act belongs to, e.g. `email.write`.
   *
   * When an act needs approval, the host may apply the answer to the whole group rather
   * than asking again for each act in a chain. The group is **declared here, never inferred
   * from the tool's name.** That is deliberate: inferring it required tool names to look
   * like `domain.action`, which imposed a naming convention nobody documented, silently
   * failed for every real name a host sends, and could never work at all for names like
   * `read_file` that have no domain.
   *
   * Free-form. Any acts sharing a string are approved together; omit it and each act is
   * approved on its own.
   */
  category?: string;
}

export interface PolicyBundlePayload {
  org_id: string;
  agent_key: string;
  environment: GovernanceEnvironment;
  version: string;
  etag: string;
  issued_at: string;
  not_before: string;
  expires_at: string;
  grace_ms: number;
  default_tier: GovernanceTier;
  tools: Record<string, ToolPolicy>;
  tool_categories?: Record<string, ToolPolicy>;
}

export interface SignedPolicyBundle {
  bundle_id: string;
  alg: 'HS256';
  key_id: string;
  signature: string;
  payload: PolicyBundlePayload;
}

export interface EvaluateRequest {
  agent_key: string;
  tool_name: string;
  environment?: GovernanceEnvironment;
  resource_hints?: Record<string, unknown>;
  correlation_id?: string;
}

export interface EvaluateResult {
  decision: EvaluateDecision;
  tier: GovernanceTier;
  requires_approval: boolean;
  reasons: string[];
  bundle_version: string;
  correlation_id: string;
  pdp_state: PdpState;
  /**
   * The approval group this decision belongs to, or null when the act stands alone.
   * Hosts that support group approval use this as the key their allowlist remembers.
   */
  category: string | null;
}

export interface PdpOptions {
  baseUrl: string;
  token: string;
  agentKey: string;
  environment?: GovernanceEnvironment;
  /** Shared secret for HS256 bundle verification */
  verifyKey: string;
  fetchTimeoutMs?: number;
  /** Optional injectors for tests */
  fetchImpl?: typeof fetch;
  now?: () => number;
}

export interface GovernancePdp {
  refresh(): Promise<{
    packVersion: string;
    bundleVersion: string;
    state: PdpState;
  }>;
  getContentPack(): GovernanceContentPack | null;
  getBundleMeta(): {
    version: string;
    expiresAt: string;
    state: PdpState;
  } | null;
  evaluate(req: EvaluateRequest): EvaluateResult;
  /** Last successfully verified bundle (for advanced hosts) */
  getBundle(): SignedPolicyBundle | null;
}
