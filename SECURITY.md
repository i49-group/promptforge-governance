# Security Policy

## Reporting a vulnerability

Email **security@i49group.com** with a description, affected version or commit, and reproduction
steps. Please do not open a public issue for an unfixed vulnerability.

We aim to acknowledge within 3 business days and to agree a disclosure timeline with you. If you
would like credit in the release notes, say so and give us the name to use.

## Scope

**In scope** — this repository: the `hermes-plugin/` Python PEP, `packages/governance-pdp`,
`packages/plugin-hermes`, the conformance vectors, and the published documentation.

**Out of scope** — the hosted PromptForge product (report those to the same address, noting that it
is the product rather than the SDK), and the agent runtimes this plugin attaches to (report those
upstream).

## Known limitations — please read before reporting

The following are **documented design boundaries**, not defects. They are stated in the
[threat model](README.md#threat-model) and we would rather discuss how to narrow them than receive
them as findings.

| Limitation | Why it is not a defect |
|---|---|
| Policy decisions ignore tool arguments | By design: `evaluate()` is keyed on tool identity. Argument-level policy is unimplemented, and its absence is disclosed. |
| A granted general-purpose tool (shell, code interpreter, HTTP client) can reproduce a denied specific tool | Follows from the above. Denying a name does not deny a capability. Mitigation is policy design, not code. |
| Delegation to an ungoverned agent bypasses a denial | The plugin governs the agent it runs in. Coverage of peers is a deployment property. |
| `PF_BUNDLE_VERIFY_KEY` is symmetric, so any host that can verify can forge | Documented. Asymmetric signing is the intended path and is not yet implemented. |
| An operator with host access can disable the plugin | It is a plugin inside the runtime it governs, not a sandbox. |
| The plugin cannot prove the host fires `pre_tool_call` on every dispatch path | Stated as a precondition the operator must verify. |

**We are interested in reports that these boundaries are wider than documented** — for example a
dispatch path that skips the hook on a runtime we claim to support, a way to defeat signature
verification, a case where an unreachable server fails *open* rather than closed, a bundle-expiry or
grace-window miscalculation that extends a stale policy, or a way to make the plugin report a
decision it did not make.

## What we consider security-critical

Changes to these paths get extra review, and a regression in any of them is a security bug:

- Signature verification and algorithm checking (`hermes-plugin/pdp.py`, `packages/governance-pdp`)
- Expiry, grace-window, and fail-closed state derivation
- The block directive returned to the host on deny
- The default-off state of `inline_approval` and `report_decisions`
- Cross-language decision equivalence (`conformance/vectors.json`)

## Supported versions

Pre-1.0. Only `main` receives fixes. Pin a commit and read the diff before upgrading.
