# PromptForge Governance

**Public SDKs and PEP plugins** for enforcing PromptForge AI governance on agent hosts.

> **PromptForge governs · your runtime acts · your ops platform executes.**

This repository is **not** the PromptForge product (PMP UI, org database, signing).  
It is what you install on **Hermes**, **OpenClaw**, Brilliant Central, or custom hosts so tool calls cannot bypass policy.

| Package | Role |
|---------|------|
| [`@promptforge/governance-pdp`](packages/governance-pdp) | Local Policy Decision Point — refresh, verify, cache, `evaluate()` |
| [`@promptforge/plugin-hermes`](packages/plugin-hermes) | Hermes PEP — gate **tool dispatch** (not SOUL text) |
| `@promptforge/plugin-openclaw` | Coming next — same pattern |

## Why a plugin (not a SOUL line)

Putting “obey PromptForge” in a SOUL or system prompt is **not** enforcement. An agent can be told to ignore it.

Enforcement is code on the **tool path**:

```
agent wants tool → PEP plugin → PDP.evaluate(signed bundle) → allow | require_approval | deny → executor
```

If the host never reaches the executor on deny, “ignore PromptForge” does nothing.

## Docs (public)

| Guide | Audience |
|-------|----------|
| [AI Governance Configuration Guide](docs/guides/ai-governance-configuration-guide.md) | Org admins — configure packages in PromptForge |
| [Import example JSON](docs/guides/ai-governance-package-import.example.json) | Appendix — paste into New package |
| [Hermes Plugin Implementation Guide](docs/guides/hermes-plugin-implementation-guide.md) | Operators — **install & verify** Act enforcement on Hermes |
| [Hermes overview](docs/hermes.md) | Short pointer to the install guide |

## Quick start (Hermes Agent)

```bash
git clone https://github.com/i49-group/promptforge-governance.git
cd promptforge-governance
hermes plugins install ./hermes-plugin --enable
# set PF_BASE_URL, PF_SERVICE_TOKEN, PF_BUNDLE_VERIFY_KEY, PF_AGENT_KEY
```

Full steps, fleet profiles, deny proof, troubleshooting:

→ **[Hermes Plugin Implementation Guide](docs/guides/hermes-plugin-implementation-guide.md)**

Native plugin: [`hermes-plugin/`](hermes-plugin/) (Python `pre_tool_call` → `{"action":"block",…}`).  
Node/TypeScript hosts: [`packages/plugin-hermes`](packages/plugin-hermes).

## Environment

| Variable | Purpose |
|----------|---------|
| `PF_BASE_URL` | e.g. `https://www.mypromptforge.com` |
| `PF_SERVICE_TOKEN` | Org service credential (`pf_svc_…`) with `governance` scope |
| `PF_BUNDLE_VERIFY_KEY` | Shared HS256 secret (same as PromptForge `GOVERNANCE_BUNDLE_SIGNING_KEY` for trusted first-party hosts) |

Never commit tokens or verify keys.

## Doctrine

| Plane | Resource | Soft / hard |
|-------|----------|-------------|
| **Talk** | Content pack (SOUL layers) | Soft — prefer PF; fallback OK |
| **Act** | Signed policy bundle | Hard — cache → grace → **fail-closed deny** |

## Development

```bash
pnpm install
pnpm test
pnpm typecheck
```

## License

MIT — see [LICENSE](LICENSE).
