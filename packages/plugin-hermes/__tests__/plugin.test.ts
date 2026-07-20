import {
  createHermesGovernancePlugin,
  GovernanceDeniedError,
} from '../src';
import type { SignedPolicyBundle } from '@promptforge/governance-pdp';
import { signPayloadHs256 } from '@promptforge/governance-pdp';

const SECRET = 'test-signing-secret-for-hermes-plugin';

function makeBundle(
  tools: SignedPolicyBundle['payload']['tools']
): SignedPolicyBundle {
  const payload = {
    org_id: 'org_test',
    agent_key: 'penn',
    environment: 'production' as const,
    version: 'vtest',
    etag: 'W/"vtest"',
    issued_at: new Date().toISOString(),
    not_before: new Date().toISOString(),
    expires_at: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
    grace_ms: 60 * 60 * 1000,
    default_tier: 'efficiency' as const,
    tools,
  };
  return {
    bundle_id: 'b_test',
    alg: 'HS256',
    key_id: 'test',
    signature: signPayloadHs256(payload, SECRET),
    payload,
  };
}

function mockFetch(bundle: SignedPolicyBundle): typeof fetch {
  const pack = {
    agent_key: 'penn',
    environment: 'production',
    version: 'pack1',
    etag: 'W/"pack1"',
    published_at: new Date().toISOString(),
    source: 'database',
    contexts: [
      {
        id: '1',
        name: 'soul',
        context_type: 'soul',
        content: '# Penn',
        content_hash: 'abc',
        tags: ['agent:penn'],
      },
    ],
    prompts: [],
    assembly: {
      order: ['soul', 'principles', 'operational', 'knowledge'],
    },
  };

  return async (input) => {
    const url = String(input);
    const body = url.includes('/bundles/')
      ? { success: true, data: bundle }
      : { success: true, data: pack };
    return {
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => body,
      text: async () => JSON.stringify(body),
    } as Response;
  };
}

describe('createHermesGovernancePlugin', () => {
  it('allows granted tools and denies unknown tools', async () => {
    const bundle = makeBundle({
      'calendar.get_events': {
        tier: 'velocity',
        requires_approval: false,
        granted: true,
      },
    });

    const plugin = createHermesGovernancePlugin({
      baseUrl: 'https://example.test',
      token: 'tok',
      verifyKey: SECRET,
      agentKey: 'penn',
      refreshIntervalMs: 0,
      fetchImpl: mockFetch(bundle),
    });

    await plugin.start();

    const allow = await plugin.beforeToolCall({
      toolName: 'calendar.get_events',
    });
    expect(allow.proceed).toBe(true);
    expect(allow.decision).toBe('allow');

      const deny = await plugin.beforeToolCall({ toolName: 'email.send' });
      expect(deny.proceed).toBe(false);
      expect(deny.decision).toBe('deny');
      expect(deny.message).toContain('TOOL BLOCKED BY PROMPTFORGE');
      expect(deny.message).toContain('What to do:');
      expect(deny.message).toContain('admin/ai-governance');

    const exec = plugin.wrapToolExecutor('email.send', async () => 'ok');
    await expect(exec({})).rejects.toBeInstanceOf(GovernanceDeniedError);

    expect(plugin.getTalkSystemPrompt()).toContain('# Penn');
    expect(plugin.hooks.before_tool_call).toBeDefined();

    plugin.stop();
  });

  it('maps require_approval through onRequireApproval', async () => {
    const bundle = makeBundle({
      'funnel.publish': {
        tier: 'efficiency',
        requires_approval: true,
        granted: true,
      },
    });

    const plugin = createHermesGovernancePlugin({
      baseUrl: 'https://example.test',
      token: 'tok',
      verifyKey: SECRET,
      agentKey: 'penn',
      refreshIntervalMs: 0,
      fetchImpl: mockFetch(bundle),
      onRequireApproval: async () => 'approved',
    });

    await plugin.start();
    const gate = await plugin.beforeToolCall({ toolName: 'funnel.publish' });
    expect(gate.proceed).toBe(true);
    expect(gate.decision).toBe('allow');
    plugin.stop();
  });
});
