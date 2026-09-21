# Implementer's Guide — Governing One Agent

**Audience:** someone putting PromptForge Act enforcement on a host, or
writing a Policy Enforcement Point of their own.

**This is not the install walkthrough.** Place the plugin, set env, and
deny-proof in the
[Hermes Plugin Implementation & Install Guide](./hermes-plugin-implementation-guide.md).
This document is the best-practice layer: what has to be true for
enforcement to be real, and what the console will show when it is.

`<agent_key>` must match the Hermes profile name and the PromptForge
profile, character for character.

---

## 1. What you are putting in place

Three things, in this order.

1. **A PEP on the tool path.** `pre_tool_call` → local PDP `evaluate` →
   block when denied. Not a SOUL line. The model cannot talk past a
   blocked tool.
2. **One gateway process per agent.** That process owns one
   `PF_AGENT_KEY`. Do not share env or a plugin directory across
   profiles.
3. **A declaration** that this agent *should* be enforced
   (`enforcement_expected` on the PromptForge profile). Without that
   flag, a missing PEP and a dormant agent look the same.

Talk (content pack) is soft. Act (signed bundle) is hard: cache →
grace → **fail-closed deny**. If you only inject pack text, you have
not enforced anything.

---

## 2. The enforcement-point contract

PromptForge cannot see your process. It can only see the HTTP you send
when you fetch a pack or a bundle.

### 2.1 Headers — on the **fetch**, not the decision

| Header | What to put | Why |
|---|---|---|
| `X-PromptForge-Pep-Build` | Content hash of *this PEP's* runtime sources (12 hex is enough) | The pack is versioned. The enforcing *code* is not, unless you send this. |
| `X-PromptForge-Pep-Version` | Human label (`1.5.0`) | Logs. The standing check compares **build**, not this. |
| `X-PromptForge-Pep-Instance` | Per-process id, new on every start | Two copies of the same build are otherwise one row. |
| `X-PromptForge-Pep-Role` | `gateway` \| `interactive` \| `dashboard` \| `command` \| `service` | Residency is judged by **role and span**, not by how many times something fetched. A human CLI session beside a gateway is a legitimate second PEP. |

Send them on **every** `GET /api/governance/packs/…` and
`GET /api/governance/bundles/…`.

Do **not** send them only when you POST a decision.
`report_decisions` is per-agent opt-in. Plain allows are sampled. The
signal that the agent is *present* has to live on a channel nobody
can switch off.

This kit's Python plugin does this in `hermes-plugin/build.py` →
`build_headers()`, attached in `pdp.py` on each fetch.

### 2.2 Absent means absent

A missing header is stored as missing. Do not invent a default build,
instance, or role "so the row looks complete". A request with no
build is either an old PEP or something that is not a PEP.

### 2.3 One hash map per artifact class

If a second service also evaluates this agent's bundle (a relying
party), it runs **different software**. Compare builds only within a
class (gateway plugin vs service PDP). Mixing them reports drift
between two current, unrelated programs.

### 2.4 `service` is a claim, not a proof

A relying party that sends `X-PromptForge-Pep-Role: service` is exempt
from the one-gateway-per-agent rule. That exemption rests on the
process's own word. Treat an undeclared service enforcer as a warning,
not a pass.

---

## 3. What "done" looks like in the console

The enforcement console enumerates states. It does not treat the
fleet as a boolean.

| Policy published | PEP reporting | Declared (`enforcement_expected`) | State | What to do |
|---|---|---|---|---|
| yes | yes | yes | **governed** | This is the point. |
| yes | no | **yes** | **promise broken** | You said it would be enforced. Find the PEP. |
| yes | yes | no | **off the record** | Something is governing an agent nobody declared. |
| yes | no | no | **awaiting enforcement** | Normal while you install. Investigate if it is still true weeks later. |
| no | no | no | **unauthored** | Write the policy first. |
| no | no | no, no fetches | dormant | Out of the headline. |

Two more situations to recognize:

- A PEP is fetching **and** there is no published policy. That is not
  governed. Something checked in; a policy does not exist yet.
- History unreadable (events exist but cannot be attributed) is not
  an empty org and is not a quiet afternoon.

**Declaration is what makes an absence a fault.** A published policy
with no plugin is *awaiting*, not a failure, until someone promised
enforcement. After the allowance it is *loud* awaiting. It is never
"healthy because nothing failed".

Level 0 is a census, not a fault total:

`<N> agents · <g> governed · <a> awaiting · <u> unauthored`

---

## 4. Worked example — one agent, end to end

Do this once before you author a fleet.

### 4.1 PromptForge

1. Create the agent profile. `agent_key` = the Hermes profile name.
2. Author the act policy from the host's **live tool registration**,
   not from memory. The name the host sends is the only name that
   matches.
3. Publish through the product editor, or through
   `publishNextVersion`
   (`scripts/governance/lib/policy-publish.ts`).
   Do not insert `status: 'published'` by hand. That skips the
   act-inventory and alias guards. The pack still builds; the dead
   name does nothing.
4. Set `enforcement_expected`. Until you do, a missing PEP is not a
   fault.
5. Confirm pack and bundle return 200 for that `agent_key` with the
   org service token.

### 4.2 Host

1. Place the plugin in **this profile's** `plugins/` directory.

   ```bash
   mkdir -p ~/.hermes/profiles/<agent>/plugins
   ln -sfn /path/to/hermes-plugin \
           ~/.hermes/profiles/<agent>/plugins/promptforge-governance
   ```

   A global enable flag cannot place it. An empty profile `plugins/`
   directory means **no decision point exists**. The gateway still
   runs. Tools still run. PromptForge still shows a published policy.

2. Enable tool-override on **that** profile. Native tools run
   ungoverned without it.

3. Put `PF_BASE_URL`, `PF_SERVICE_TOKEN`, `PF_BUNDLE_VERIFY_KEY`,
   `PF_AGENT_KEY`, `PF_ENVIRONMENT` on the **process** that will
   survive restart (launchd plist / systemd unit). A file you
   `source` in a shell does not reach a supervised gateway.

   **One process = one `PF_AGENT_KEY`.** Every agent gets its own
   gateway. Do not share env across profiles.

4. Restart **only** that gateway. Confirm the five `PF_*` values on
   the process, not in your shell.

### 4.3 Prove it

1. Pack + bundle curl from the host return 200.
2. Access events for this agent show `pep_build`, `pep_instance`,
   `pep_role`. If those fields are empty, you are fetching without
   the headers — the install "works" and the console cannot see you.
3. Trigger a denied tool. Hermes returns the block to the model.
   If `report_decisions` is on, a row lands in Decisions.
4. Enforcement console: this agent is **governed**, not awaiting,
   not off the record.

If step 4 still says awaiting, the declaration is missing or the
fetch is not reaching PromptForge.

---

## 5. Best practices

### 5.1 One gateway per agent

One supervised process, one `PF_AGENT_KEY`, one profile `plugins/`
directory. Sharing a process or env across profiles makes two agents
look like one enforcement point — or one agent look like two.

Restart only the gateway you changed. Confirm env on that process.

### 5.2 Place the plugin on the profile

The plugin must live in each profile's own `plugins/` directory.
`ls ~/.hermes/profiles/<agent>/plugins` empty means nothing will
ever decide.

### 5.3 Heartbeat on the fetch path

Send identity headers on every pack/bundle GET. Decision reporting
is optional and sampled. An ungoverned agent looks like a quiet day
unless presence is on a channel that cannot be switched off.

### 5.4 Identify the enforcer, not just the agent

- Hash the PEP sources. A hand-bumped `__version__` will be forgotten.
- Instance id is per process. Restarting must change it. "How many
  are running now" is distinct ids still fetching, not ids that have
  ever appeared.
- Judge residency by **role and span**. Short-lived CLI invocations
  load the plugin and fetch once; they are not extra gateways.

### 5.5 Author the names the host actually sends

Write acts from the runtime's own tool registration. Gating a name
the host has never sent (`process_manage` while the host sends
`process`) is a control that never fires.

Use **one spelling per act** — the name your runtime dispatches.
The evaluator accepts aliases; carrying both spellings is how acts
become unmatchable. A near-miss (wrong case, single underscores, a
leading space) will not match and will not deny.

### 5.6 Derive facets from arguments

A `terminal` whose command is `curl` is also `terminal.network`. A
`process` whose action is `kill` is `process.kill`. If you only
gate the host name, every gate is bypassable through a shell.

Evaluate the **most restrictive** of the host name plus every
*listed* derived act. If the policy names no derived act, the base
decision stands. Unlisted facets are recorded, never denied.
Derivation tightens only when the policy opts in.

If your checkout of this kit has no `derive.py`, you still owe this
behavior. Fetch headers alone are not facet derivation.

### 5.7 Publish through the product path

Two writers may set `status: 'published'`:

- `scripts/governance/lib/policy-publish.ts` (`publishNextVersion`)
- the editor, `PolicyService.publish()`

A direct insert skips the act-inventory and alias guards.

When you republish, **carry every flag you did not intend to
change**. Omitting `report_decisions` or `inline_approval` resets
them to the column default (`false`). A policy with reporting off
produces no decisions, which looks like a quiet afternoon.

### 5.8 Declare before you expect a fault

`enforcement_expected` is the promise. Set it when the PEP is meant
to be live. Until then, absence is awaiting, not healthy and not a
break.

### 5.9 Fail closed, and prove the deny

No bundle, expired grace, or verify failure → deny. Reporting must
never delay or fail the tool call.

Proof is a denied tool on the host **and** `pep_*` fields on the
access event **and** **governed** on the console. A 200 from pack
curl is necessary and not sufficient.

---

## 6. Writing a PEP of your own

If you are not using this Hermes plugin, you still owe the contract
in §2.

Minimum:

1. Local evaluate against the signed bundle. Fail closed with no
   bundle, after grace, or on a verify failure.
2. Fetch headers on every pack/bundle GET.
3. Derive extra act names from arguments when the host name is a
   multiplexer (`terminal`, `process`, `execute_code`). Evaluate
   the **most restrictive** of the name plus every *listed* facet.
   If the policy names no derived act, the base decision stands.
4. Report decisions only when the bundle says `report_decisions`.
   Never let reporting delay or fail the tool call.
5. One process, one `agent_key`.

---

## 7. What the PromptForge console will and will not tell you

| Surface | Tells you | Will not tell you |
|---|---|---|
| `/admin/governance/enforcement` | Census + worst finding, then depth on demand | A 0–100 score. Per-check green rows at Level 0. |
| `/admin/governance` Compliance | Isolation (labelled), RBAC (labelled), audit trail, inheritance | Training / ZDR "measured". Integrations "connected". |
| Decisions tab | Rows the PEP posted, if opted in | A complete allow trail (allows are sampled). |
| Access events | Who fetched, with which build | That a tool was allowed or denied. |

---

## 8. Related

| Doc | Use |
|---|---|
| [Install guide](./hermes-plugin-implementation-guide.md) | Place the plugin, set env, deny-proof |
| [Configuration guide](./ai-governance-configuration-guide.md) | Org-admin package setup in PromptForge |
