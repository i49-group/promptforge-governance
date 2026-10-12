# `@i49-group/governance-pdp`

Local **Policy Decision Point** for PromptForge governance.

> MCP and REST distribute packs/bundles. **Evaluate stays in-process.**

## Install

```bash
npm install @i49-group/governance-pdp
```

Every version is published from this repository's `publish` workflow with an npm provenance
statement naming the commit it was built from. Check what you installed:

```bash
npm audit signatures
```

## Usage

```ts
import { createGovernancePdp } from '@i49-group/governance-pdp';

const pdp = createGovernancePdp({
  baseUrl: 'https://www.mypromptforge.com',
  token: process.env.PF_SERVICE_TOKEN!,
  agentKey: 'my-agent',
  environment: 'staging',
  verifyKey: process.env.PF_BUNDLE_VERIFY_KEY!, // same as GOVERNANCE_BUNDLE_SIGNING_KEY for HS256
  fetchTimeoutMs: 500,
  // Optional: sent on every fetch, e.g. PromptForge's enforcement-point identity headers.
  headers: { 'x-promptforge-pep-role': 'service' },
});

await pdp.refresh(); // session start + periodic

const decision = pdp.evaluate({
  agent_key: 'my-agent',
  tool_name: 'email.schedule_campaign',
  correlation_id: 'corr_…',
});
// allow | require_approval | deny
```

## State machine

`normal` → `cached` (refresh failed, still before expiry) → `grace` → `fail_closed` (deny).

The content pack (Talk) and the policy bundle (Act) load independently: a missing pack never
stops the bundle.

## No policy versus unreachable

With no bundle loaded, every `evaluate` denies. `getLastBundleFetch()` says why, so a host can
record the right reason:

| Value | Meaning |
|---|---|
| `ok` | The last fetch loaded a verified bundle |
| `not_found` | PromptForge answered 404: it has no policy for this agent |
| `failed` | PromptForge could not be reached, timed out, or answered another error |
| `unverified` | A bundle arrived but its signature did not verify |
| `null` | No refresh has run yet |

## Tests

```bash
pnpm --filter @i49-group/governance-pdp test
```

## Guides

- [Implementer's guide](https://github.com/i49-group/promptforge-governance/blob/main/docs/guides/implementers-guide.md)
- [AI governance configuration guide](https://github.com/i49-group/promptforge-governance/blob/main/docs/guides/ai-governance-configuration-guide.md)
