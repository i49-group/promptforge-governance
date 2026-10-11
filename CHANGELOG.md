# Changelog

`@i49-group/governance-pdp` and `@i49-group/plugin-hermes` share one version.

## 0.2.0

First version published to npm.

- **Renamed** from `@promptforge/*` to `@i49-group/*`. The `@promptforge` name on npm belongs to an
  unrelated publisher, so the old install lines pointed at a name this project does not control.
- **`getLastBundleFetch()`** reports how the last bundle fetch ended: `ok`, `not_found` (PromptForge
  has no policy for this agent), `failed` (unreachable or another error), or `unverified`. A host can
  now record "no policy" and "unreachable" as different facts.
- **`headers` option** adds headers to every fetch, e.g. PromptForge's enforcement-point identity
  headers.
- **Pack and bundle load independently.** A missing or failed content pack no longer stops the
  policy bundle from loading.
- **Published with provenance** from the `publish` workflow.

## 0.1.0

Source only, never published.
