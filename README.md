# PromptForge Governance

**Public SDKs and PEP plugins** for enforcing PromptForge AI governance on agent hosts.

> **PromptForge governs · your runtime acts · your ops platform executes.**

This repository is **not** the PromptForge product (PMP UI, org database, signing).  
It is what you install on **Hermes**, **OpenClaw**, or custom hosts to gate tool dispatch against
signed policy.

**Before deploying, read the [threat model](#threat-model).** It states what this enforces, what it
does not, and what it trusts. The short version: policy is evaluated on tool *identity*, not on
arguments, so it constrains what an agent may *call* — not everything an agent may *do*.

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
                   └─ receives the tool NAME; arguments are not evaluated ─┘
```

If the host never reaches the executor on deny, "ignore PromptForge" does nothing — **provided** the
four preconditions in [No-bypass preconditions](#no-bypass-preconditions) hold. They are not
automatic, and three of them are properties of your host rather than of this plugin.

## Threat model

### What it enforces

**Tool dispatch, by name.** On each `pre_tool_call` the plugin resolves the tool name against a
signed policy bundle and returns allow, require_approval, or deny. On deny the host does not reach
the executor.

**Availability failures close, not open.** The bundle is signed, cached, and carries an expiry. If
the governance server is unreachable the plugin serves the cached bundle until expiry, then a grace
window, then denies every act. An unreachable server never becomes an open door. This is tested
(`hermes-plugin/test_pdp.py`, `packages/governance-pdp/__tests__/`).

### What it does not enforce

**1. Arguments. Policy is evaluated on tool identity alone.**

`evaluate()` receives the tool name and a correlation id. It does not receive, and cannot consider,
the call's arguments. `email.send` to one recipient and the same tool to fifty thousand are the same
decision. If you need those to differ, they must be different tool names.

**The corollary matters more than the limitation, and is easy to miss.** If an agent holds a
general-purpose tool — a shell, a code interpreter, an HTTP client — that tool can reproduce the
effect of most specifically-named tools. Denying `browser.exec` while granting `shell.run` denies a
*label*, not a *capability*: the same fetch is one `curl` away. Denying a process-management tool
while granting a shell denies nothing at all.

So: **tool-name policy is only as strong as the broadest tool the agent holds.** A policy that
denies twenty narrow tools while allowing a shell has documented an intention, not imposed a
constraint.

**Partial mitigation: act-name derivation.** For the tools that name a *mechanism* rather than a
capability, the Hermes plugin narrows the name before the PDP sees it — a `terminal` call running
`curl` is offered as `terminal.network`, one signalling a process as `terminal.process`, one
scheduling work as `terminal.schedule`. Several facets can apply at once and the most restrictive
governs. This keeps the PDP purely name-based while making the name specific enough to bind. See
[`hermes-plugin/derive.py`](hermes-plugin/derive.py).

Three things to understand about it:

- **It is opt-in per policy.** Until a policy lists `terminal.network`, a `curl` resolves to
  `terminal` exactly as before, so this can be deployed to a running fleet without denying a call.
  Adding the derived act is what activates the constraint.
- **It does not make policy argument-aware.** The decision is still made on a name. Recipient
  counts, monetary amounts, and record scope remain invisible to it.
- **It is pattern-based, therefore incomplete.** A command shape outside the pattern set derives
  nothing and falls back to the base act. When that happens the decision records
  `unclassified` — or `args_unavailable` if the host passed no arguments at all — so
  a gap is visible in the decision trail rather than being silently indistinguishable from a
  call that had nothing to narrow. Treat a derived-act policy as raising the cost of substitution,
  not as closing it.

**2. Peers.** An agent that can delegate to another agent can ask that agent to do what it was
denied. If the peer is ungoverned, the denial is advisory — the work happens, one hop away, with no
decision recorded against the agent that wanted it. Either enforce on **every** agent reachable by
delegation, or treat the delegation tool itself as privileged (deny it, or require approval).

**3. The host.** This is a plugin running inside the runtime it governs. An operator who can edit
the host's configuration can disable it; a process that can write the plugin directory can replace
it. It raises the cost of unsanctioned action inside a trusted runtime — it is not a sandbox, and it
offers no protection against a compromised host or a hostile operator.

**4. Instruction-level compliance.** The Talk pack (content) is soft by design: the model may be
influenced by it but is not bound to it. Only the Act plane (the signed bundle, on the tool path) is
enforced. Never rely on prompt text for a control you need to hold.

### What it trusts

**The bundle signing key is symmetric (HS256), so verification and forgery are the same
capability.** `PF_BUNDLE_VERIFY_KEY` is the same secret PromptForge signs with. Any host holding it
can mint a bundle granting itself anything, and the plugin will verify that bundle as authentic.

This is why the key is documented for **first-party hosts you already trust with the agent's
credentials** — a host in that position can generally act directly anyway, so the key grants little
it did not already have. It is nonetheless the sharpest edge here, and it means:

- Do not distribute the verify key to hosts outside your trust boundary.
- Do not treat a valid signature as proof of *origin* across such a boundary. It proves only that
  the signer held the shared secret.
- Asymmetric signing (Ed25519/RS256), which would make verify keys non-forging and allow untrusted
  hosts to verify without being able to sign, is **not implemented**. It is the intended path for
  third-party deployment.

The plugin also trusts the host to call `pre_tool_call` on every dispatch path and to honour a block
directive. See below.

### No-bypass preconditions

The "cannot bypass" claim holds only while all four are true. Verify each on your host:

1. **Every** executor path fires `pre_tool_call` — including bridged, nested, and code-interpreter
   tool invocations. One unhooked path is an open path.
2. Your runtime version honours a block directive. Some do not; on those, a deny is logged and the
   tool still runs. The implementation guide's §8 checklist covers how to prove which you have.
3. The plugin is enabled on **every** profile you intend to govern. A provisioned credential is not
   evidence that a plugin is loaded — check registration in the logs, per profile.
4. No granted tool can reach the capability a denied tool represents (see limitation 1).

Points 1–3 are properties of your deployment, and point 4 is a property of your policy. This plugin
can verify none of them for you.

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
| `PF_BUNDLE_VERIFY_KEY` | Shared HS256 secret (same as PromptForge `GOVERNANCE_BUNDLE_SIGNING_KEY`). **Symmetric — a host holding this can also forge bundles. First-party hosts only; see [What it trusts](#what-it-trusts).** |
| `PF_AGENT_KEY` | Which agent's policy to fetch |
| `PF_ENVIRONMENT` | Bundle environment (default `production`) |
| `PF_REFRESH_SECONDS` | Background refresh interval (default `300`) |
| `PF_FETCH_TIMEOUT_S` | Per-request timeout for policy fetches (default `2`) |
| `PF_INJECT_TALK` | Inject the content pack into the system prompt (default `true`) |

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
