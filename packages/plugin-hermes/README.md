# `@promptforge/plugin-hermes`

**Policy Enforcement Point (PEP)** for [Hermes](https://github.com/NousResearch/hermes) (and Hermes-compatible harnesses).

This package does **not** put rules in SOUL.md. It gates **tool dispatch** using `@promptforge/governance-pdp` against a signed PromptForge policy bundle.

## Install

```bash
pnpm add @promptforge/plugin-hermes @promptforge/governance-pdp
```

## Wire into Hermes

Call `beforeToolCall` (or register `plugin.hooks`) on **every** tool path — including MCP, local skills, and shell. Any ungated path is ungoverened.

```ts
import { createHermesGovernancePlugin } from '@promptforge/plugin-hermes';

const gov = createHermesGovernancePlugin({
  baseUrl: process.env.PF_BASE_URL!,
  token: process.env.PF_SERVICE_TOKEN!,
  verifyKey: process.env.PF_BUNDLE_VERIFY_KEY!,
  agentKey: process.env.PF_AGENT_KEY || 'penn',
  environment: 'production',
  onRequireApproval: async ({ toolName }) => {
    // Your Hermes / human approval UX
    return 'denied';
  },
});

await gov.start();

// Option A — explicit gate
const gate = await gov.beforeToolCall({ toolName: 'calendar.create_event' });
if (!gate.proceed) throw new Error(gate.message);

// Option B — wrap executors
const safeCreate = gov.wrapToolExecutor('calendar.create_event', createEvent);

// Option C — hook bag for hosts that support it
// registerPlugin(gov) or harness.use(gov.hooks)
```

## Talk (optional)

`getTalkSystemPrompt()` concatenates pack layers (soul → principles → operational → knowledge). Prefer this for identity; **never** treat it as Act enforcement.

## Docs

- [Hermes install guide](../../docs/hermes.md)
- [Org admin configuration](../../docs/guides/ai-governance-configuration-guide.md)
