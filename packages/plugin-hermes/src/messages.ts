import type { EvaluateResult } from '@promptforge/governance-pdp';

export const GUIDE_URL =
  'https://github.com/i49-group/promptforge-governance/blob/main/docs/guides/ai-governance-configuration-guide.md';
export const INSTALL_URL =
  'https://github.com/i49-group/promptforge-governance/blob/main/docs/guides/hermes-plugin-implementation-guide.md';
export const ADMIN_URL = 'https://www.mypromptforge.com/admin/ai-governance';

function bullets(steps: string[]): string {
  return steps.map((s, i) => `  ${i + 1}. ${s}`).join('\n');
}

export function classifySetupError(exc: unknown): string {
  const text = String(exc).toLowerCase();
  if (text.includes('missing') && text.includes('pf_')) return 'missing_env';
  if (
    text.includes('401') ||
    text.includes('unauthorized') ||
    text.includes('403') ||
    text.includes('forbidden')
  ) {
    return 'auth';
  }
  if (text.includes('404') || text.includes('not found')) return 'not_found';
  if (text.includes('signature') || text.includes('verify')) return 'signature';
  if (
    text.includes('timed out') ||
    text.includes('timeout') ||
    text.includes('network') ||
    text.includes('fetch')
  ) {
    return 'network';
  }
  return 'setup';
}

export function setupBlockMessage(
  toolName: string,
  agentKey: string,
  exc: unknown
): string {
  const kind = classifySetupError(exc);
  const detail = String(exc).trim();

  const headers: Record<string, string> = {
    missing_env:
      'PromptForge governance is installed, but required environment variables are missing. Tool calling is blocked until they are set.',
    auth: 'PromptForge rejected this host’s credentials. Tool calling is blocked until a valid org service token is configured.',
    not_found: `No PromptForge governance package is available for agent_key \`${agentKey}\` (pack/bundle not found). Tool calling is blocked until a package exists with published Act policy.`,
    signature:
      'PromptForge policy bundle signature verification failed. Tool calling is blocked until PF_BUNDLE_VERIFY_KEY matches the PromptForge signing key.',
    network:
      'Could not reach PromptForge to load governance policy. Tool calling is blocked (fail-closed) until connectivity is restored.',
    setup:
      'PromptForge governance could not load a policy bundle. Tool calling is blocked until governance is configured.',
  };

  const steps: Record<string, string[]> = {
    missing_env: [
      'Set PF_BASE_URL, PF_SERVICE_TOKEN, PF_BUNDLE_VERIFY_KEY, and PF_AGENT_KEY.',
      `Use PF_AGENT_KEY=\`${agentKey}\` matching the PromptForge package.`,
      `Follow: ${INSTALL_URL}`,
    ],
    auth: [
      'In PromptForge: Admin → Integrations → create an org service credential with `governance` scope.',
      'Put the token in PF_SERVICE_TOKEN and restart the host.',
      'Confirm the token belongs to the same org that owns the AI Governance package.',
    ],
    not_found: [
      `Open ${ADMIN_URL}`,
      `Create or open the package for agent_key \`${agentKey}\` (New package).`,
      'Ensure Talk layers exist and Act policy is Published (not draft-only).',
      `Config guide: ${GUIDE_URL}`,
    ],
    signature: [
      'Copy GOVERNANCE_BUNDLE_SIGNING_KEY from the PromptForge deploy into PF_BUNDLE_VERIFY_KEY.',
      'Restart the host after updating the key.',
      `See: ${INSTALL_URL}`,
    ],
    network: [
      'Check that this host can reach PF_BASE_URL (default https://www.mypromptforge.com).',
      'Retry after network is up; tools stay blocked until refresh succeeds.',
    ],
    setup: [
      `Open ${ADMIN_URL} and confirm a package for \`${agentKey}\` with published Act.`,
      'Verify PF_BASE_URL / PF_SERVICE_TOKEN / PF_BUNDLE_VERIFY_KEY / PF_AGENT_KEY.',
      `Guides: ${GUIDE_URL} · ${INSTALL_URL}`,
    ],
  };

  return [
    '🚫 TOOL BLOCKED BY PROMPTFORGE',
    `Tool: \`${toolName}\``,
    `Agent: \`${agentKey}\``,
    '',
    headers[kind],
    '',
    'What to do:',
    bullets(steps[kind]),
    '',
    `Technical detail: ${detail}`,
  ].join('\n');
}

export function policyBlockMessage(
  toolName: string,
  agentKey: string,
  result: EvaluateResult
): string {
  const reasons = result.reasons || [];
  const reasonL = reasons.map((r) => r.toLowerCase());
  const joined = reasons.length ? reasons.join(', ') : 'policy';
  const version = result.bundle_version || 'unknown';
  const state = result.pdp_state || 'unknown';

  let title: string;
  let steps: string[];

  if (
    result.decision === 'require_approval' ||
    reasonL.some((r) => r.includes('requires_approval'))
  ) {
    title =
      'This tool requires human approval under PromptForge Act policy.';
    steps = [
      'Have an operator approve the action in your ops host (e.g. Brilliant Central), or',
      `In PromptForge (${ADMIN_URL}) open agent \`${agentKey}\` → Act policy → set requires_approval=false for \`${toolName}\` if intentional → Publish.`,
      'Then retry (hosts refresh bundles periodically).',
    ];
  } else if (reasonL.some((r) => r.includes('unknown_tool'))) {
    title = `\`${toolName}\` is not in the published Act inventory for \`${agentKey}\`.`;
    steps = [
      `Open ${ADMIN_URL} → package \`${agentKey}\` → Act policy.`,
      `Add tool \`${toolName}\` with the correct tier / granted / requires_approval.`,
      'Click Publish, then retry after bundle refresh.',
    ];
  } else if (reasonL.some((r) => r.includes('not_granted'))) {
    title = `\`${toolName}\` is explicitly not granted in PromptForge Act policy.`;
    steps = [
      `Open ${ADMIN_URL} → package \`${agentKey}\` → Act policy.`,
      `Set granted=true for \`${toolName}\` (if appropriate) → Publish.`,
      'Retry after bundle refresh.',
    ];
  } else if (
    reasonL.some((r) => r.includes('fail_closed')) ||
    state === 'fail_closed'
  ) {
    title =
      'PromptForge policy cache is expired past grace (fail-closed). Tool calling stays blocked until a fresh signed bundle is loaded.';
    steps = [
      'Confirm PromptForge is reachable and Act is still published.',
      'Check PF_SERVICE_TOKEN; restart the host to force refresh.',
      `Install help: ${INSTALL_URL}`,
    ];
  } else {
    title = `PromptForge Act policy denied \`${toolName}\`.`;
    steps = [
      `Review Act policy for \`${agentKey}\` at ${ADMIN_URL}.`,
      'Adjust granted / tier / requires_approval → Publish.',
      `Config guide: ${GUIDE_URL}`,
    ];
  }

  return [
    '🚫 TOOL BLOCKED BY PROMPTFORGE',
    `Tool: \`${toolName}\``,
    `Agent: \`${agentKey}\``,
    `Decision: ${result.decision} · Bundle: ${version} · PDP state: ${state}`,
    '',
    title,
    '',
    'What to do:',
    bullets(steps),
    '',
    `Reasons: ${joined}`,
    'Tell the user clearly that PromptForge blocked this tool and summarize the steps above.',
  ].join('\n');
}

export function gateMessage(
  result: EvaluateResult,
  toolName: string,
  agentKey: string
): string {
  if (result.decision === 'allow') {
    return `PromptForge allowed ${toolName} (bundle ${result.bundle_version})`;
  }
  return policyBlockMessage(toolName, agentKey, result);
}
