import type {
  EvaluateDecision,
  EvaluateResult,
  GovernancePdp,
  PdpState,
} from '@promptforge/governance-pdp';

export type ApprovalOutcome = 'approved' | 'denied';

export interface HermesGovernancePluginOptions {
  baseUrl: string;
  token: string;
  verifyKey: string;
  agentKey: string;
  environment?: 'staging' | 'production';
  fetchTimeoutMs?: number;
  /** Periodic bundle refresh. Default 10 minutes. Set 0 to disable. */
  refreshIntervalMs?: number;
  /** Inject fetch for tests */
  fetchImpl?: typeof fetch;
  /**
   * Called when PDP returns require_approval.
   * Return approved to proceed; denied to treat as deny.
   * If omitted, require_approval is returned to the caller (does not auto-execute).
   */
  onRequireApproval?: (ctx: ToolGateContext & { result: EvaluateResult }) => Promise<ApprovalOutcome>;
}

export interface ToolGateContext {
  toolName: string;
  correlationId?: string;
  args?: Record<string, unknown>;
  resourceHints?: Record<string, unknown>;
}

export interface ToolGateResult {
  decision: EvaluateDecision;
  /** True only when the tool may execute (allow, or approved after require_approval). */
  proceed: boolean;
  message: string;
  evaluate: EvaluateResult;
  bundleVersion: string;
  pdpState: PdpState;
}

export interface HermesGovernancePlugin {
  readonly name: 'promptforge-governance';
  readonly agentKey: string;
  /** Load pack+bundle and start refresh loop. */
  start(): Promise<void>;
  stop(): void;
  getPdp(): GovernancePdp;
  /**
   * Call immediately before Hermes executes a tool.
   * This is the Act enforcement point — not SOUL/Talk.
   */
  beforeToolCall(ctx: ToolGateContext): Promise<ToolGateResult>;
  /**
   * Wrap an existing async tool executor so every call is gated.
   */
  wrapToolExecutor<TArgs extends Record<string, unknown>, TResult>(
    toolName: string,
    executor: (args: TArgs) => Promise<TResult>
  ): (args: TArgs) => Promise<TResult>;
  /**
   * Optional Talk assembly from content pack (prefer-PF). Not a security boundary.
   */
  getTalkSystemPrompt(): string | null;
  /** Hermes-style hook object for hosts that register `{ hooks.before_tool_call }`. */
  hooks: {
    before_tool_call: (ctx: {
      tool_name?: string;
      name?: string;
      correlation_id?: string;
      arguments?: Record<string, unknown>;
    }) => Promise<ToolGateResult>;
  };
}

export class GovernanceDeniedError extends Error {
  readonly decision = 'deny' as const;
  readonly toolName: string;
  readonly bundleVersion: string;
  readonly pdpState: PdpState;
  readonly reasons: string[];

  constructor(gate: ToolGateResult, toolName: string) {
    super(gate.message);
    this.name = 'GovernanceDeniedError';
    this.toolName = toolName;
    this.bundleVersion = gate.bundleVersion;
    this.pdpState = gate.pdpState;
    this.reasons = gate.evaluate.reasons;
  }
}
