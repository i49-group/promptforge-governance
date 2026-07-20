# hermes-minimal

Demonstrates `@promptforge/plugin-hermes` against a live PromptForge org.

```bash
# from repo root
pnpm install
cp examples/hermes-minimal/.env.example examples/hermes-minimal/.env
# edit .env

cd examples/hermes-minimal
set -a && source .env && set +a
pnpm start
```

Expected: refresh succeeds; a granted tool may `allow`; an unknown tool `deny` with `proceed: false`.
