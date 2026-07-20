# AI Governance Configuration Guide

**Audience:** Organization administrators planning to use PromptForge to govern digital workers (agents)  
**Product URL:** [Admin → AI Governance](https://www.mypromptforge.com/admin/ai-governance)  
**Last updated:** 2026-07-19

---

## 1. What PromptForge does (and does not do)

PromptForge is your **Policy Management Point (PMP)** for AI agents:

| PromptForge owns | Your platforms own |
|------------------|--------------------|
| **Talk** — who the agent is (identity, principles, ops, knowledge) | Running the agent (Hermes, OpenClaw, Cursor, custom apps) |
| **Act** — which tools are allowed, at what risk tier, with/without approval | Executing tools against your CRM, mail, calendar, etc. |
| Signing and distributing packages your runtimes pull | Approvals UX, audit in your ops systems |

PromptForge does **not** send email, host chat, or call your CRM. It authors, versions, and signs the package; your runtime enforces it.

### Doctrine in one line

**PromptForge governs · your agent runtime acts · your ops platform executes.**

---

## 2. Core concepts

| Term | Meaning |
|------|---------|
| **agent_key** | Stable ID shared with your platform (e.g. `penn`, `leo`). Used in APIs and Context tags. |
| **AI Governance Profile** | One binder per agent: Talk + Act + distribution Activity. |
| **Talk / content pack** | Assembled Context layers: `soul`, `principles`, `operational`, `knowledge`. |
| **Act / policy bundle** | Signed rules for tools and tiers. Cached by a local PDP on the host that enforces decisions. |
| **Publish** | Makes a new Act version live for PEP hosts. Talk updates when you save Contexts (no separate Publish). |
| **TTL / Grace** | How long a host may use a cached Act bundle before it must refresh (then fail-closed / deny). |
| **Org service credential** | `pf_svc_…` token for production hosts (not a personal MCP PAT). |

Everything lives in **your organization’s database**. There is no code-shipped “demo agent” fallback: until you create a package and publish Act (and have Talk layers), pack/bundle APIs return **not found**.

---

## 3. Prerequisites

1. **Role:** `org_admin` (or `owner` / `super_admin`) on the PromptForge organization that will own the agents.  
2. **Plan:** AI Governance / MCP features enabled for your plan (ask your PromptForge contact if the Admin → AI Governance page is missing).  
3. **agent_keys:** Agree with your engineering team on the IDs your platforms already use (or will use).  
4. **Tool inventory (recommended):** A JSON list of tool names your platform exposes for each agent (names must match what the runtime sends to evaluate).

---

## 4. End-to-end setup checklist

Use this once per environment (today: production).

1. [ ] Create an **org service credential** (Integrations) with `governance` (+ `mcp` if using MCP).  
2. [ ] For each agent: **New package** in AI Governance (blank or import Act JSON).  
3. [ ] Edit **Talk** layers in Context Manager (keep tag `agent:<key>`).  
4. [ ] Configure **Act** tools/tiers → **Publish**.  
5. [ ] Give your platform team: base URL, service token, agent_keys, and the signing verification approach they need.  
6. [ ] Confirm **Activity** shows pack/bundle pulls after the host refreshes.  
7. [ ] On the host: show “Governed by PromptForge” with the **bundle version** from Act.

---

## 5. Create a package (New package)

**Path:** Admin → **AI Governance** → **New package**

Two first-class ways to configure:

| Path | What you do | Best when |
|------|-------------|-----------|
| **A. Manual (blank)** | Create blank → edit tools/tiers in **Act policy** → Publish; write Talk in Context Manager | Exploring, small inventories, or policy owned entirely in the UI |
| **B. JSON import** | Choose **Import Act from platform inventory JSON** → paste [Appendix A](#appendix-a--importable-package-json) (or your platform export) → optional Publish on create | Engineering already has a tool inventory; you want Act preconfigured |

Talk stubs are always created on create. JSON import preconfigures **Act** (and can fill display name / email / platform). You still author Talk content in Context Manager.

### Fields

| Field | Guidance |
|-------|----------|
| **agent_key** | Lowercase ID matching your platform (`leo`, not `Leo`). Cannot change later without a new profile. |
| **Display name** | Human label in the admin UI. (Also accepted from JSON on import.) |
| **Digital worker email** | Optional identity email (e.g. `leo@agents.yourcompany.com`) carried on the pack. |
| **Target platform** | Operator note only (e.g. `brilliant_central`) — stored for your team. |

### How to seed

| Mode | Talk | Act | When to use |
|------|------|-----|-------------|
| **Blank** | Empty stub layers | Empty policy **draft** | Path A — configure Act in the UI |
| **Import Act from platform inventory JSON** | Same stubs | Draft or published policy from JSON | Path B — paste Appendix A (or your export) |

After create:

1. Open **Package** to confirm Talk layers exist.  
2. Open **Act policy** — for blank mode, add tools then **Publish**. For import, review tiers and publish if you left “Publish Act immediately” unchecked.  
3. Edit Talk content in **Context Manager** (link from Package tab).

Tool names in Act must match what your PEP sends to evaluate. Mismatches deny. Schema + paste-ready examples: [Appendix A](#appendix-a--importable-package-json).

---

## 6. Configure Talk (content pack)

Talk is edited in **Context Manager** (`/prompt-contexts`), not as a free-form blob on the governance page.

### Layers (assembly order)

1. **soul** — Identity and role  
2. **principles** — Non-negotiables  
3. **operational** — How the agent works day to day  
4. **knowledge** — Domain facts, references, boundaries  

### Tags (required)

- Always include `agent:<agent_key>` (e.g. `agent:penn`).  
- Optional environment scoping: `env:production` or `env:staging`. If any `env:*` tags exist on a layer, the pack request’s environment must match.

### Publishing Talk

Saving an active Context updates the pack version/etag automatically. There is no separate “Publish Talk” button.

If a package has no Talk layers, use **Create Talk stubs** on the Package tab, or create Contexts manually with the correct tags.

---

## 7. Configure Act (policy)

**Path:** Profile → **Act policy**

For each tool:

| Field | Meaning |
|-------|---------|
| **granted** | If false, evaluate → deny (tool not in the agent’s inventory for governance). |
| **tier** | `velocity` (low risk) · `efficiency` · `control` (high risk). Used for reporting and host UX. |
| **requires_approval** | If true, evaluate → require_approval (your host must get a human OK before execute). |

Also set:

- **Default tier** — Used when a tool is not listed (prefer listing every tool explicitly).  
- **TTL** — Fresh cache window (e.g. 6 hours).  
- **Grace** — Extra window after TTL before fail-closed deny.

**Save draft** keeps work private. **Publish** activates a new Act version for all hosts that refresh.

---

## 8. Credentials for company integrations

**Path:** Admin → **Integrations** → Service credentials

| Use | Credential |
|-----|------------|
| Production PEP host (Brilliant Central, Hermes bridge, custom backend) | Org service token (`pf_svc_…`) with `governance` scope |
| Personal Claude Desktop / Cursor as a user | Personal MCP PAT under Settings — **not** for company backends |

Rotate any token that was pasted into chat or tickets. Activity attributes pulls to the principal that called the APIs.

---

## 9. How your platforms integrate (admin view)

You do not need to implement the PDP yourself, but you should know the contract so you can brief engineering and verify go-live.

### What hosts pull

| Resource | Purpose | Offline behavior |
|----------|---------|------------------|
| **Content pack** | Talk layers for the agent | Host may keep last-known Talk |
| **Signed policy bundle** | Act rules for evaluate | Cache → grace → **deny** (fail-closed) |

### Typical architecture

```
PromptForge (PMP)
  → pack + signed bundle
      → Local PDP on your host (verify · cache · evaluate)
          → PEP enforces allow / require_approval / deny
              → Your systems execute
```

### Protocol choice (for your engineers)

| Host type | Preferred pull | Hot-path decide |
|-----------|----------------|-----------------|
| Agent harness (Hermes, Cursor, Claude) | MCP / REST pack+bundle | **PEP plugin** + local PDP on cached bundle |
| Platform backend (e.g. BC) | REST | Same `@promptforge/governance-pdp` before execute |

Base URL (production): `https://www.mypromptforge.com`

### Hermes Act enforcement (required for hard governance)

SOUL / Talk text is **not** enough — agents can be told to ignore it. Install the Hermes PEP plugin so tools cannot run without `evaluate`:

- **Install guide:** [Hermes Plugin Implementation Guide](./hermes-plugin-implementation-guide.md)
- Native plugin: [`hermes-plugin/`](../../hermes-plugin/) (`hermes plugins install ./hermes-plugin --enable`)

OpenClaw and other hosts follow the same pattern (thin plugin over the shared PDP).

---

## 10. Verify distribution (Activity)

**Path:** Profile → **Activity**

After the host refreshes, you should see pack and/or bundle events with outcome success (or not_modified when etags match).

If Activity is empty:

1. Confirm the service credential scopes and org.  
2. Confirm `agent_key` matches the profile.  
3. Confirm Talk layers exist and Act is **published**.  
4. Ask engineering to check host logs for 401/403/404 on pack/bundle.

---

## 11. Day-2 operations

| Change | Where | What hosts see |
|--------|-------|----------------|
| Update identity / SOUL text | Context Manager | New pack version after save |
| Allow a new tool | Act policy → Publish | New bundle version |
| Tighten approval | Act policy → Publish | New bundle version after refresh/TTL |
| Retire an agent | Set profile inactive + revoke host config | Hosts should stop requesting that key |
| Add a new agent | New package | New agent_key end-to-end |

**Do not** create a second profile for the same `agent_key`. Edit the existing one.

---

## 12. Roles and boundaries

| Role | Can |
|------|-----|
| Org admin | Create packages, edit Talk (org Contexts), publish Act, manage service credentials |
| Members | Usually edit Contexts per your org’s Context permissions — not the governance binder |
| Super admin | Platform ops; same governance UI when acting in an org |

Related but different:

- **Model & Content Governance** (`/admin/governance`) — folders, sharing, training — not agent Talk/Act packages.

---

## 13. Go-live acceptance criteria

For each agent_key:

- [ ] Profile exists and status is **active**  
- [ ] Talk: four layers (or intentional subset) tagged `agent:<key>`, content reviewed  
- [ ] Act: published policy; tool names match platform inventory  
- [ ] Org service credential issued to the host team only  
- [ ] Activity shows successful pack + bundle pull  
- [ ] Host footer (or equivalent) shows PromptForge bundle version  
- [ ] Spot-check: denied tool denies; approval-gated tool requires approval  

---

## 14. FAQ

**Why did pack/bundle return 404?**  
No tagged Talk layers and/or no published Act for that org + agent_key.

**Can we copy Talk from another agent?**  
Duplicate Contexts in Context Manager and retag `agent:<new_key>`, or start from New package stubs and paste content.

**Does Publish change Talk?**  
No. Publish is Act only.

**Personal MCP vs service credential?**  
Personal = human-in-the-loop IDE. Service = company backend. Use service for production.

**Where do engineers install Hermes enforcement?**  
[Hermes Plugin Implementation Guide](./hermes-plugin-implementation-guide.md).

**Can I re-import JSON later to overwrite Act?**  
Not as a bulk replace today. After create, change Act in the UI (draft → Publish). For a brand-new `agent_key`, import again via New package.

---

## Related links

- Admin UI: `/admin/ai-governance`  
- Context Manager: `/prompt-contexts`  
- Integrations (credentials): `/admin/integrations`  
- Standalone example file: [`ai-governance-package-import.example.json`](./ai-governance-package-import.example.json)  
- Hermes PEP: [`docs/hermes.md`](../hermes.md)  
- This public repo: SDK + plugins + these guides

---

## Appendix A — Importable package JSON

Paste this into **New package → Import Act from platform inventory JSON**, or start from the file [`ai-governance-package-import.example.json`](./ai-governance-package-import.example.json). Replace `agent_key`, emails, and tool names with your platform’s inventory before create.

### Schema

| Field | Required | Notes |
|-------|----------|--------|
| `tools` | **Yes** | Object map `tool_name` → policy, **or** array of strings / `{ name\|tool_key, tier?, … }` |
| `agent_key` | Recommended | Prefer matching the form field; use the same key your PEP will call |
| `display_name` | No | Fills display name if the form field is empty |
| `digital_worker_email` | No | Fills email if the form field is empty |
| `platform` or `platform_source` | No | Stored as an operator note |
| `default_tier` | No | `velocity` \| `efficiency` \| `control` (default `efficiency`) |
| `tool_categories` | No | Optional category-level defaults (same shape as a tool policy) |
| `ttl_ms` / `grace_ms` | No | Optional; UI/service defaults apply if omitted |

Each tool policy object:

| Field | Default | Meaning |
|-------|---------|---------|
| `tier` | `efficiency` | Risk band |
| `requires_approval` | `true` | Host must get human OK before execute |
| `granted` | `true` | `false` → evaluate denies |

On create, leave **Publish Act immediately** checked to go live, or uncheck to review the draft first. Talk stubs are always created; JSON does not replace SOUL/principles content.

### Minimal example (two tools)

```json
{
  "agent_key": "demo-agent",
  "display_name": "Demo Agent",
  "digital_worker_email": "demo@agents.example.com",
  "platform_source": "your_platform",
  "default_tier": "efficiency",
  "tools": {
    "calendar.get_events": {
      "tier": "velocity",
      "requires_approval": false,
      "granted": true
    },
    "crm.update_contact": {
      "tier": "control",
      "requires_approval": true,
      "granted": true
    }
  }
}
```

### Full example (Leo-shaped inventory — paste as-is to try import)

```json
{
  "agent_key": "leo",
  "display_name": "Leo — Kingdom Marketing AI",
  "digital_worker_email": "leo@agents.brilliantperspectives.com",
  "platform": "brilliant_central",
  "default_tier": "velocity",
  "tools": {
    "calendar.get_events": {
      "tier": "velocity",
      "requires_approval": false,
      "granted": true
    },
    "calendar.create_event": {
      "tier": "efficiency",
      "requires_approval": true,
      "granted": true
    },
    "calendar.update_event": {
      "tier": "efficiency",
      "requires_approval": true,
      "granted": true
    },
    "campaign.get_clarity_insights": {
      "tier": "velocity",
      "requires_approval": false,
      "granted": true
    },
    "campaign.refresh_ad_data": {
      "tier": "velocity",
      "requires_approval": false,
      "granted": true
    },
    "funnel.analytics_read": {
      "tier": "velocity",
      "requires_approval": false,
      "granted": true
    },
    "funnel.create": {
      "tier": "velocity",
      "requires_approval": false,
      "granted": true
    },
    "funnel.generate_page": {
      "tier": "velocity",
      "requires_approval": false,
      "granted": true
    },
    "funnel.regenerate_step": {
      "tier": "velocity",
      "requires_approval": false,
      "granted": true
    },
    "funnel.publish": {
      "tier": "efficiency",
      "requires_approval": true,
      "granted": true
    },
    "funnel.update_checkout_config": {
      "tier": "efficiency",
      "requires_approval": true,
      "granted": true
    }
  },
  "tool_categories": {
    "calendar.read": {
      "tier": "velocity",
      "requires_approval": false,
      "granted": true
    },
    "calendar.write": {
      "tier": "efficiency",
      "requires_approval": true,
      "granted": true
    },
    "analytics.read": {
      "tier": "velocity",
      "requires_approval": false,
      "granted": true
    }
  }
}
```

> If `leo` already exists in your org, change `agent_key` (and email) before importing, or use **Blank** and edit Act manually on the existing profile.
