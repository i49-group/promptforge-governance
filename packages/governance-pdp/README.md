# `@promptforge/governance-pdp`

Local **Policy Decision Point** for PromptForge governance.

> MCP and REST distribute packs/bundles. **Evaluate stays in-process.**

## Install (monorepo)

```bash
pnpm add @promptforge/governance-pdp --workspace
```

## Usage

```ts
import { createGovernancePdp } from '@promptforge/governance-pdp';

const pdp = createGovernancePdp({
  baseUrl: 'https://www.mypromptforge.com',
  token: process.env.PF_SERVICE_TOKEN!,
  agentKey: 'penn',
  environment: 'staging',
  verifyKey: process.env.PF_BUNDLE_VERIFY_KEY!, // same as GOVERNANCE_BUNDLE_SIGNING_KEY for HS256
  fetchTimeoutMs: 500,
});

await pdp.refresh(); // session start + periodic

const decision = pdp.evaluate({
  agent_key: 'penn',
  tool_name: 'email.schedule_campaign',
  correlation_id: 'corr_…',
});
// allow | require_approval | deny
```

## State machine

`normal` → `cached` (refresh failed, still before expiry) → `grace` → `fail_closed` (deny).

## Tests

```bash
pnpm --filter @promptforge/governance-pdp test
```

## Specs

- `docs/architecture/SDK-GOVERNANCE-PDP-SURFACE.md`
- `docs/architecture/KEY_PF-GOVERNANCE-PACK-AND-BUNDLE-API.md`
- `docs/guides/developer/governance-integration-guide.md`
