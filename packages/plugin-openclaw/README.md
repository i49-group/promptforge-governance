# `@i49-group/plugin-openclaw`

**Status:** Planned (after Hermes proves the PEP pattern).

Same architecture as [`@i49-group/plugin-hermes`](../plugin-hermes):

1. Depend on `@i49-group/governance-pdp`
2. Wrap OpenClaw **tool dispatch** (not SOUL text)
3. `allow` / `require_approval` / `deny` + fail-closed

See [Hermes guide](../../docs/hermes.md) for the contract to mirror.
