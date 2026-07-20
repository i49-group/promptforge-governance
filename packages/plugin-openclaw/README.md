# `@promptforge/plugin-openclaw`

**Status:** Planned (after Hermes proves the PEP pattern).

Same architecture as [`@promptforge/plugin-hermes`](../plugin-hermes):

1. Depend on `@promptforge/governance-pdp`
2. Wrap OpenClaw **tool dispatch** (not SOUL text)
3. `allow` / `require_approval` / `deny` + fail-closed

See [Hermes guide](../../docs/hermes.md) for the contract to mirror.
