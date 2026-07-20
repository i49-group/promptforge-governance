import { createGovernancePdp } from '@promptforge/governance-pdp';
import { gateMessage } from './messages';
import {
  GovernanceDeniedError,
  type HermesGovernancePlugin,
  type HermesGovernancePluginOptions,
  type ToolGateContext,
  type ToolGateResult,
} from './types';

const DEFAULT_REFRESH_MS = 10 * 60 * 1000;

const ASSEMBLY_ORDER = [
  'soul',
  'principles',
  'operational',
  'knowledge',
] as const;

export function createHermesGovernancePlugin(
  options: HermesGovernancePluginOptions
): HermesGovernancePlugin {
  const agentKey = options.agentKey.trim().toLowerCase();
  const refreshIntervalMs =
    options.refreshIntervalMs === undefined
      ? DEFAULT_REFRESH_MS
      : options.refreshIntervalMs;

  const pdp = createGovernancePdp({
    baseUrl: options.baseUrl,
    token: options.token,
    verifyKey: options.verifyKey,
    agentKey,
    environment: options.environment,
    fetchTimeoutMs: options.fetchTimeoutMs,
    fetchImpl: options.fetchImpl,
  });

  let timer: ReturnType<typeof setInterval> | null = null;
  let started = false;

  async function beforeToolCall(ctx: ToolGateContext): Promise<ToolGateResult> {
    const toolName = ctx.toolName.trim();
    const result = pdp.evaluate({
      agent_key: agentKey,
      tool_name: toolName,
      correlation_id: ctx.correlationId,
      resource_hints: ctx.resourceHints ?? ctx.args,
    });

    const base: ToolGateResult = {
      decision: result.decision,
      proceed: false,
      message: gateMessage(result, toolName, agentKey),
      evaluate: result,
      bundleVersion: result.bundle_version,
      pdpState: result.pdp_state,
    };

    if (result.decision === 'allow') {
      return { ...base, proceed: true };
    }

    if (result.decision === 'require_approval') {
      if (!options.onRequireApproval) {
        return base;
      }
      const outcome = await options.onRequireApproval({ ...ctx, result });
      if (outcome === 'approved') {
        return {
          ...base,
          decision: 'allow',
          proceed: true,
          message: `PromptForge approval granted for ${toolName} (bundle ${result.bundle_version})`,
        };
      }
      return {
        ...base,
        decision: 'deny',
        proceed: false,
        message: gateMessage(
          { ...result, decision: 'deny', reasons: ['approval_denied'] },
          toolName,
          agentKey
        ),
      };
    }

    return base;
  }

  const plugin: HermesGovernancePlugin = {
    name: 'promptforge-governance',
    agentKey,
    async start() {
      await pdp.refresh();
      started = true;
      if (refreshIntervalMs > 0) {
        timer = setInterval(() => {
          void pdp.refresh().catch((err) => {
            console.warn(
              '[promptforge-governance] refresh failed:',
              err instanceof Error ? err.message : err
            );
          });
        }, refreshIntervalMs);
        if (typeof timer === 'object' && 'unref' in timer) {
          timer.unref();
        }
      }
    },
    stop() {
      if (timer) {
        clearInterval(timer);
        timer = null;
      }
      started = false;
    },
    getPdp() {
      return pdp;
    },
    beforeToolCall,
    wrapToolExecutor(toolName, executor) {
      return async (args) => {
        if (!started) {
          await plugin.start();
        }
        const gate = await beforeToolCall({
          toolName,
          args: args as Record<string, unknown>,
        });
        if (!gate.proceed) {
          throw new GovernanceDeniedError(gate, toolName);
        }
        return executor(args);
      };
    },
    getTalkSystemPrompt() {
      const pack = pdp.getContentPack();
      if (!pack?.contexts?.length) return null;
      const byType = new Map(
        pack.contexts.map((c) => [c.context_type, c.content])
      );
      const parts: string[] = [];
      for (const type of ASSEMBLY_ORDER) {
        const content = byType.get(type);
        if (content?.trim()) {
          parts.push(content.trim());
        }
      }
      if (parts.length === 0) return null;
      return parts.join('\n\n---\n\n');
    },
    hooks: {
      before_tool_call: async (ctx) => {
        const toolName = String(ctx.tool_name || ctx.name || '').trim();
        if (!toolName) {
          const deny: ToolGateResult = {
            decision: 'deny',
            proceed: false,
            message: 'PromptForge denied: missing tool_name',
            evaluate: {
              decision: 'deny',
              tier: 'control',
              requires_approval: true,
              reasons: ['missing_tool_name'],
              bundle_version: pdp.getBundleMeta()?.version || 'none',
              correlation_id: ctx.correlation_id || 'missing-tool',
              pdp_state: pdp.getBundleMeta()?.state || 'fail_closed',
            },
            bundleVersion: pdp.getBundleMeta()?.version || 'none',
            pdpState: pdp.getBundleMeta()?.state || 'fail_closed',
          };
          return deny;
        }
        return beforeToolCall({
          toolName,
          correlationId: ctx.correlation_id,
          args: ctx.arguments,
        });
      },
    },
  };

  return plugin;
}
