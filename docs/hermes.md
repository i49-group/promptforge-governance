# Hermes — PromptForge governance plugin

**Audience:** Operators wiring Hermes so PromptForge **Act** policy is enforced  
**Package:** [`@promptforge/plugin-hermes`](../packages/plugin-hermes)

## What this does

Gates **every tool call** with a local PDP against a signed PromptForge policy bundle.

It does **not** rely on SOUL.md or “please obey PromptForge.” If Hermes can execute a tool without this gate, that path is ungoverened.

```
Hermes agent → beforeToolCall / wrapToolExecutor → PDP.evaluate → allow | require_approval | deny → executor
```

## Prerequisites

1. PromptForge org with an **AI Governance Profile** for the `agent_key` (Talk + **published** Act).  
2. Org service credential (`pf_svc_…`) with `governance` scope.  
3. `PF_BUNDLE_VERIFY_KEY` shared with PromptForge `GOVERNANCE_BUNDLE_SIGNING_KEY` (trusted first-party).  
4. See [AI Governance Configuration Guide](./guides/ai-governance-configuration-guide.md).

## Install

```bash
pnpm add @promptforge/plugin-hermes @promptforge/governance-pdp
```

## Environment

```bash
PF_BASE_URL=https://www.mypromptforge.com
PF_SERVICE_TOKEN=pf_svc_…
PF_BUNDLE_VERIFY_KEY=…
PF_AGENT_KEY=penn
```

## Integration patterns

### 1. Explicit gate (recommended starting point)

```ts
import { createHermesGovernancePlugin } from '@promptforge/plugin-hermes';

const gov = createHermesGovernancePlugin({
  baseUrl: process.env.PF_BASE_URL!,
  token: process.env.PF_SERVICE_TOKEN!,
  verifyKey: process.env.PF_BUNDLE_VERIFY_KEY!,
  agentKey: process.env.PF_AGENT_KEY || 'penn',
  environment: 'production',
});

await gov.start();

async function runTool(toolName: string, args: Record<string, unknown>) {
  const gate = await gov.beforeToolCall({ toolName, args });
  if (!gate.proceed) {
    throw new Error(gate.message);
  }
  return executeTool(toolName, args); // your existing Hermes executor
}
```

### 2. Wrap each executor

```ts
const safeSend = gov.wrapToolExecutor('email.schedule_campaign', scheduleCampaign);
```

### 3. Hook bag

If your Hermes build registers plugins with `hooks.before_tool_call`:

```ts
registerHooks(gov.hooks);
// or: harness.use(gov)
```

Exact registration depends on your Hermes version — the **contract** is: no tool runs unless `proceed === true`.

## Approvals

Tools with `requires_approval: true` return `require_approval` unless you supply `onRequireApproval`:

```ts
onRequireApproval: async ({ toolName, result }) => {
  // Show human UX; return 'approved' | 'denied'
  return askHuman(toolName, result);
},
```

For BC MCP tools, you may keep approval UX in Brilliant Central and still evaluate here (or evaluate only in BC for Path 2). Pick **one** PEP per executor path — never fail-open.

## Talk (optional)

```ts
const talk = gov.getTalkSystemPrompt();
if (talk) systemPrompt = talk;
```

Prefer-PF-with-fallback is fine for Talk. **Act must fail-closed.**

## No-bypass checklist

- [ ] MCP tools gated  
- [ ] Local skills / Meta Ads / shell gated  
- [ ] Degraded / admin chat paths gated if they share executors  
- [ ] Fail-closed when bundle missing or past grace  
- [ ] Footer or log shows `bundleVersion` from gate results  

## Example

See [`examples/hermes-minimal`](../examples/hermes-minimal).
