# Hermes Plugin — Implementation & Install Guide

**Audience:** Operators installing PromptForge Act enforcement on [Hermes Agent](https://hermes-agent.nousresearch.com/)  
**Plugin path in this repo:** [`hermes-plugin/`](../../hermes-plugin/)  
**Hermes docs:** [Plugins](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins) · [Hooks](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks)

---

## 1. What you are installing

A **Hermes-native Python plugin** that is a Policy Enforcement Point (PEP):

| Layer | Behavior |
|-------|----------|
| **Act (hard)** | `pre_tool_call` → local PDP `evaluate` → return `{"action":"block","message":…}` when denied |
| **Talk (soft)** | Optional `pre_llm_call` injects content-pack text (prefer-PF; not security) |

This is **not** a SOUL.md rule. The model cannot talk past a blocked tool.

```
Hermes tool dispatch
  → promptforge-governance pre_tool_call
      → verify + evaluate signed PF bundle (local cache)
          → allow: tool runs
          → deny / require_approval / fail-closed: Hermes returns error to the model
```

---

## 2. Prerequisites

### 2.1 Hermes

- Hermes Agent with plugin support and **`pre_tool_call` block** semantics  
  (`return {"action": "block", "message": "…"}` — see Hermes hooks docs / PR [#9377](https://github.com/NousResearch/hermes-agent/pull/9377)).
- Confirm CLI works:

```bash
hermes --version
hermes plugins list
```

### 2.2 PromptForge (per agent)

For each `agent_key` (e.g. `penn`, `leo`):

1. [AI Governance](https://www.mypromptforge.com/admin/ai-governance) → profile exists  
2. Talk layers tagged `agent:<key>` (or stubs from New package)  
3. **Act policy published** (pack/bundle APIs return 200)  
4. Org service credential `pf_svc_…` with `governance` scope  

See [AI Governance Configuration Guide](./ai-governance-configuration-guide.md).

### 2.3 Secrets (Hermes host)

| Variable | Meaning |
|----------|---------|
| `PF_BASE_URL` | `https://www.mypromptforge.com` |
| `PF_SERVICE_TOKEN` | Org service token (`pf_svc_…`) |
| `PF_BUNDLE_VERIFY_KEY` | Same value as PromptForge `GOVERNANCE_BUNDLE_SIGNING_KEY` (trusted first-party HS256) |
| `PF_AGENT_KEY` | Must match the Hermes profile’s agent (e.g. `penn`) |
| `PF_ENVIRONMENT` | Optional; default `production` |
| `PF_INJECT_TALK` | Optional; default `true` — set `false` to skip Talk injection |
| `PF_REFRESH_SECONDS` | Optional; default `600` — set `0` to disable background refresh |

Never commit these values. Prefer Hermes `requires_env` prompts, launchd/plist env, or a root-owned env file.

---

## 3. Install the plugin

Hermes installs plugins from Git into `~/.hermes/plugins/` and loads them only when **enabled**.

This monorepo keeps the Hermes plugin under `hermes-plugin/` (not the repo root), so use one of the methods below.

### Method A — Local path install (recommended for Brilliant Mac Mini)

```bash
# On the Hermes host
cd ~/src   # or wherever you keep repos
git clone https://github.com/i49-group/promptforge-governance.git
cd promptforge-governance

# Install/enable the subdirectory as a Hermes plugin
hermes plugins install ./hermes-plugin --enable
```

If your Hermes CLI only accepts git URLs, symlink instead:

```bash
mkdir -p ~/.hermes/plugins
ln -sfn "$(pwd)/hermes-plugin" ~/.hermes/plugins/promptforge-governance
hermes plugins enable promptforge-governance
```

### Method B — GitHub (when cloning the subdirectory)

```bash
# Clone then point Hermes at hermes-plugin/
git clone https://github.com/i49-group/promptforge-governance.git /tmp/promptforge-governance
hermes plugins install /tmp/promptforge-governance/hermes-plugin --enable
```

### Method C — Per-profile plugin (fleet)

If each agent profile (`~/.hermes/profiles/penn`, `leo`, …) has its own config:

1. Install the plugin once under `~/.hermes/plugins/promptforge-governance`  
2. Enable it in **each** profile’s `config.yaml` that should be governed  
3. Set **`PF_AGENT_KEY`** to that profile’s key (`penn`, `leo`, …) in that profile’s environment  

```yaml
# ~/.hermes/config.yaml  (or profile-specific config)
plugins:
  enabled:
    - promptforge-governance
```

```bash
hermes plugins list
# expect: promptforge-governance · enabled
```

---

## 4. Configure environment

### 4.1 Prompted install

`plugin.yaml` lists `requires_env`. On first enable, Hermes may prompt for missing vars. Fill them for that host.

### 4.2 Launchd / gateway (production)

For gateway services (`ai.hermes.gateway-penn`, etc.), put env in the service definition or a file the process sources **before** start:

```bash
export PF_BASE_URL=https://www.mypromptforge.com
export PF_SERVICE_TOKEN=pf_svc_…
export PF_BUNDLE_VERIFY_KEY=…
export PF_AGENT_KEY=penn
export PF_ENVIRONMENT=production
```

Restart the gateway after changes:

```bash
# example — adjust to your launchd labels
launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway-penn
```

### 4.3 One agent_key per Hermes process

Do **not** share one Hermes process across `penn` and `leo` with a single `PF_AGENT_KEY`. Each profile/gateway should match its PromptForge package.

---

## 5. Verify enforcement

### 5.1 Smoke: pack + bundle reachable

From the Hermes host:

```bash
curl -sS -H "Authorization: Bearer $PF_SERVICE_TOKEN" \
  "$PF_BASE_URL/api/governance/packs/$PF_AGENT_KEY?environment=production" | head -c 400
echo
curl -sS -H "Authorization: Bearer $PF_SERVICE_TOKEN" \
  "$PF_BASE_URL/api/governance/bundles/$PF_AGENT_KEY?environment=production" | head -c 400
echo
```

Expect HTTP 200 and `success: true` both.

### 5.2 Plugin loaded

```bash
hermes plugins list
# promptforge-governance should be enabled
```

Start a session for that agent and check logs for:

```text
Registered promptforge-governance hooks
PromptForge governance ready agent=penn …
```

### 5.3 Deny a tool (hard proof)

1. In PromptForge Act policy, ensure a tool the agent might call is **not granted** (or omit it → unknown → deny).  
2. **Publish**.  
3. In Hermes chat, ask the agent to use that tool.  
4. Expect a tool error containing `PromptForge denied` — the executor must **not** run.

### 5.4 Activity in PromptForge

Admin → AI Governance → profile → **Activity** should show pack/bundle pulls after session start / refresh.

---

## 6. How decisions map

| PDP decision | Hermes plugin behavior |
|--------------|------------------------|
| `allow` | Hook returns `None` → tool executes |
| `deny` | `{"action":"block","message":"PromptForge denied …"}` |
| `require_approval` | **Block** with message (no PF approval card in Hermes yet). Use BC approval for BC MCP tools, or set `requires_approval: false` after human process. |
| No bundle / past grace | **Block** (fail-closed) |

---

## 7. Talk injection (optional)

By default the plugin injects assembled Talk (`soul` → `principles` → `operational` → `knowledge`) via `pre_llm_call`.

- Disable: `PF_INJECT_TALK=false`  
- Prefer keeping Hermes SOUL as fallback if pack fetch fails (Talk is soft).  
- **Never** treat Talk injection as Act enforcement.

---

## 8. No-bypass checklist

Before calling the fleet “governed”:

- [ ] Plugin **enabled** on every profile that can invoke tools  
- [ ] `PF_AGENT_KEY` matches PromptForge package for that profile  
- [ ] MCP tools, terminal/shell, and local skills all go through Hermes tool dispatch (same `pre_tool_call`)  
- [ ] Denied tool does not execute (verified in §5.3)  
- [ ] BC Path 2 MCP still has its **own** PEP if tools execute in BC (defense in depth)  
- [ ] Hermes version supports `pre_tool_call` **block** returns  

---

## 9. Update / rollback

```bash
cd ~/src/promptforge-governance && git pull
hermes plugins update promptforge-governance   # if installed via hermes plugins
# or: re-symlink / re-install ./hermes-plugin

hermes plugins disable promptforge-governance  # emergency rollback (ungoverned!)
```

Disabling the plugin removes Act enforcement — treat as an incident, not a convenience.

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Plugin listed but not loaded | Not in `plugins.enabled` | `hermes plugins enable promptforge-governance` |
| Every tool blocked | Missing/expired bundle or bad token | Check curl smoke; publish Act; verify `PF_*` |
| Signature verification failed | Wrong `PF_BUNDLE_VERIFY_KEY` | Match PF `GOVERNANCE_BUNDLE_SIGNING_KEY` |
| Pack 404 | No Talk layers for agent | Create Talk stubs / tag Contexts `agent:<key>` |
| Bundle 404 | No published policy | Publish Act on the profile |
| Block ignored; tool still runs | Hermes build without block support | Upgrade Hermes; confirm hooks docs for `action: block` |
| Wrong agent’s policy | Shared env across profiles | Per-gateway `PF_AGENT_KEY` |

---

## 11. Relationship to `@promptforge/plugin-hermes` (TypeScript)

| Package | Use when |
|---------|----------|
| **`hermes-plugin/`** (this guide) | Native Hermes Agent (`hermes plugins install`) |
| [`@promptforge/plugin-hermes`](../../packages/plugin-hermes) | Node/TypeScript hosts wrapping tool executors |

Same doctrine; different runtime. Both use PromptForge pack/bundle APIs and fail-closed Act.

---

## 12. Related

- [AI Governance Configuration Guide](./ai-governance-configuration-guide.md)  
- [Import JSON example](./ai-governance-package-import.example.json)  
- Plugin source: [`hermes-plugin/`](../../hermes-plugin/)  
- Minimal Node demo (SDK): [`examples/hermes-minimal`](../../examples/hermes-minimal)
