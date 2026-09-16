# Conformance vectors

`vectors.json` is the shared definition of what a PromptForge policy decision
*is*. Every evaluator, in every language, must produce exactly these results.

```bash
pnpm test:conformance
```

That runs the vectors against both evaluators in this repo — the TypeScript
reference and the Python Hermes plugin.

## Why this exists

The same decision logic is implemented several times: this repo's TypeScript
package, this repo's Python plugin, PromptForge's in-app copy, and third-party
ops backends. Nothing previously proved they agreed — and they did not.

Both Python copies omitted one rule — **an act at the `control` tier requires
approval even when its `requires_approval` flag is false**. So an act that
PromptForge and the TypeScript reference gated, Hermes allowed outright. Four
smaller disagreements sat alongside it: reason ordering, a missing `grace_period`
/ `using_cached_bundle` disclosure on allow, `granted` reported instead of the
resolved tier, and a different split for multi-segment act names.

None of it had ever changed a live decision, because no act had ever been
`control` without also being flagged. That was luck, not design, and it runs out
the moment tiers are derived from the characteristics of a process rather than
typed by hand — because then a computed `control` on an unflagged act is the
normal case, not an impossible one.

Run against the pre-fix Python evaluator, these vectors fail 16 of 24, three of
them as genuinely different authorization decisions. That is the value: the same
file, run by both languages, in CI.

## The reference implementation

[`packages/governance-pdp/src/evaluate.ts`](../packages/governance-pdp/src/evaluate.ts)
is normative. When behaviour must change, change the reference, the vectors and
every other evaluator **in the same commit**. A vector change that only one
implementation passes is a bug report, not a decision.

## Scope

**In:** the pure evaluate step — a verified bundle payload plus a PDP state in, a
decision out.

**Out:** fetching, HS256 verification, caching, and TTL/grace state derivation.
Those are per-host concerns with their own tests. A host holding no bundle at all
is also out of scope; it reports `fail_closed`, and the `fail_closed` vectors then
apply.

## Adding a case

Each case carries a `why` explaining what breaks if it is absent — the Python
runner prints it on failure, so write it for whoever hits the failure at 2am, not
for the reviewer. Reference a bundle by name from the `bundles` map rather than
inlining a payload, and prefer adding a bundle over loosening an existing one.

Cases must be deterministic: no clocks, no randomness, no network. `correlation_id`
is generated per call and is deliberately not asserted.

## Coverage today

Exact match; category fallback for both reads and writes; the read classifier
(`get_` and `list_` prefixes, and `search` matched exactly rather than by prefix);
category denial; `control` gating with and without the flag; `default_tier`
escalation; unknown acts; ungranted acts; domain-less act names (many real-world
act names have no domain); malformed names with a trailing dot; multi-segment
names; a missing `tool_categories` object; and the `fail_closed`, `grace` and
`cached` states including reason ordering when several reasons apply.

Not covered: signature verification, refresh and cache behaviour, and any
policy bundle malformed enough to omit `tier` or `granted`.
