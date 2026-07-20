/**
 * Minimal Hermes-shaped PEP demo.
 * Loads PF pack+bundle, then gates two fake tool calls.
 *
 *   cp .env.example .env  # fill tokens
 *   pnpm start
 */
import { createHermesGovernancePlugin } from '@promptforge/plugin-hermes';

async function main() {
  const baseUrl = process.env.PF_BASE_URL;
  const token = process.env.PF_SERVICE_TOKEN;
  const verifyKey = process.env.PF_BUNDLE_VERIFY_KEY;
  const agentKey = process.env.PF_AGENT_KEY || 'penn';

  if (!baseUrl || !token || !verifyKey) {
    console.error(
      'Set PF_BASE_URL, PF_SERVICE_TOKEN, PF_BUNDLE_VERIFY_KEY (see .env.example)'
    );
    process.exit(1);
  }

  const gov = createHermesGovernancePlugin({
    baseUrl,
    token,
    verifyKey,
    agentKey,
    environment: 'production',
    refreshIntervalMs: 0,
  });

  console.log(`Refreshing governance for agent_key=${agentKey} …`);
  await gov.start();

  const meta = gov.getPdp().getBundleMeta();
  console.log('Bundle:', meta);

  const talk = gov.getTalkSystemPrompt();
  console.log('Talk layers loaded:', talk ? `${talk.slice(0, 80)}…` : '(none)');

  for (const toolName of [
    'calendar.get_events',
    'definitely.not.allowed.tool',
  ]) {
    const gate = await gov.beforeToolCall({ toolName });
    console.log(
      JSON.stringify(
        {
          toolName,
          decision: gate.decision,
          proceed: gate.proceed,
          bundleVersion: gate.bundleVersion,
          pdpState: gate.pdpState,
          message: gate.message,
        },
        null,
        2
      )
    );
  }

  gov.stop();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
