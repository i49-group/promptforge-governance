import {
  createGovernancePdp,
  derivePdpState,
  evaluateAgainstBundle,
  signPayloadHs256,
  verifyPayloadHs256,
} from '../src';
import type {
  GovernanceContentPack,
  PolicyBundlePayload,
  SignedPolicyBundle,
} from '../src';

const SECRET = 'test-governance-signing-key';

function makePayload(
  overrides: Partial<PolicyBundlePayload> = {}
): PolicyBundlePayload {
  const now = Date.now();
  return {
    org_id: 'org-1',
    agent_key: 'sales-agent',
    environment: 'staging',
    version: '2026.07.19.test',
    etag: 'W/"bundle-test"',
    issued_at: new Date(now).toISOString(),
    not_before: new Date(now).toISOString(),
    expires_at: new Date(now + 60_000).toISOString(),
    grace_ms: 30_000,
    default_tier: 'efficiency',
    tools: {
      'calendar.get_events': {
        tier: 'velocity',
        requires_approval: false,
        granted: true,
      },
      'email.schedule_campaign': {
        tier: 'efficiency',
        requires_approval: true,
        granted: true,
      },
      'email.send_now': {
        tier: 'control',
        requires_approval: true,
        granted: true,
      },
      'contacts.delete': {
        tier: 'control',
        requires_approval: true,
        granted: false,
      },
    },
    ...overrides,
  };
}

function makeBundle(
  payloadOverrides: Partial<PolicyBundlePayload> = {}
): SignedPolicyBundle {
  const payload = makePayload(payloadOverrides);
  return {
    bundle_id: 'bundle-1',
    alg: 'HS256',
    key_id: 'test',
    signature: signPayloadHs256(payload, SECRET),
    payload,
  };
}

function makePack(): GovernanceContentPack {
  return {
    agent_key: 'sales-agent',
    environment: 'staging',
    version: 'pack-v1',
    etag: 'W/"pack"',
    published_at: new Date().toISOString(),
    contexts: [],
    prompts: [],
    assembly: {
      order: ['soul', 'principles', 'operational', 'knowledge'],
    },
  };
}

describe('verifyPayloadHs256', () => {
  it('accepts a valid signature', () => {
    const bundle = makeBundle();
    expect(
      verifyPayloadHs256(bundle.payload, bundle.signature, SECRET)
    ).toBe(true);
  });

  it('rejects a tampered payload', () => {
    const bundle = makeBundle();
    const tampered = {
      ...bundle.payload,
      version: 'tampered',
    };
    expect(
      verifyPayloadHs256(tampered, bundle.signature, SECRET)
    ).toBe(false);
  });
});

describe('evaluateAgainstBundle', () => {
  it('allows reads without approval (most-restrictive-wins with default_tier)', () => {
    const bundle = makeBundle();
    const result = evaluateAgainstBundle(
      bundle,
      { agent_key: 'sales-agent', tool_name: 'calendar.get_events' },
      'normal'
    );
    // tool=velocity, default=efficiency → effective efficiency; still no approval required
    expect(result.decision).toBe('allow');
    expect(result.tier).toBe('efficiency');
    expect(result.requires_approval).toBe(false);
  });

  it('requires approval for efficiency writes', () => {
    const bundle = makeBundle();
    const result = evaluateAgainstBundle(
      bundle,
      { agent_key: 'sales-agent', tool_name: 'email.schedule_campaign' },
      'normal'
    );
    expect(result.decision).toBe('require_approval');
    expect(result.requires_approval).toBe(true);
  });

  it('requires approval for control tier', () => {
    const bundle = makeBundle();
    const result = evaluateAgainstBundle(
      bundle,
      { agent_key: 'sales-agent', tool_name: 'email.send_now' },
      'normal'
    );
    expect(result.decision).toBe('require_approval');
    expect(result.tier).toBe('control');
  });

  it('denies unknown tools', () => {
    const bundle = makeBundle();
    const result = evaluateAgainstBundle(
      bundle,
      { agent_key: 'sales-agent', tool_name: 'totally.unknown' },
      'normal'
    );
    expect(result.decision).toBe('deny');
    expect(result.reasons).toContain('unknown_tool');
  });

  it('denies not-granted tools', () => {
    const bundle = makeBundle();
    const result = evaluateAgainstBundle(
      bundle,
      { agent_key: 'sales-agent', tool_name: 'contacts.delete' },
      'normal'
    );
    expect(result.decision).toBe('deny');
    expect(result.reasons).toContain('not_granted');
  });

  it('denies everything in fail_closed', () => {
    const bundle = makeBundle();
    const result = evaluateAgainstBundle(
      bundle,
      { agent_key: 'sales-agent', tool_name: 'calendar.get_events' },
      'fail_closed'
    );
    expect(result.decision).toBe('deny');
    expect(result.reasons).toContain('pdp_fail_closed');
  });
});

describe('derivePdpState', () => {
  it('returns grace when expired but within grace_ms', () => {
    const now = Date.now();
    const payload = makePayload({
      expires_at: new Date(now - 1_000).toISOString(),
      grace_ms: 60_000,
    });
    expect(derivePdpState(payload, { now })).toBe('grace');
  });

  it('returns fail_closed past grace', () => {
    const now = Date.now();
    const payload = makePayload({
      expires_at: new Date(now - 120_000).toISOString(),
      grace_ms: 30_000,
    });
    expect(derivePdpState(payload, { now })).toBe('fail_closed');
  });

  it('returns cached when refresh failed but still before expiry', () => {
    const now = Date.now();
    const payload = makePayload({
      expires_at: new Date(now + 60_000).toISOString(),
    });
    expect(derivePdpState(payload, { now, refreshFailed: true })).toBe(
      'cached'
    );
  });
});

describe('createGovernancePdp', () => {
  it('refreshes, verifies, and evaluates allow', async () => {
    const pack = makePack();
    const bundle = makeBundle();

    const fetchImpl = jest.fn(async (url: string) => {
      const body = url.includes('/packs/')
        ? { success: true, data: pack }
        : { success: true, data: bundle };
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        json: async () => body,
        text: async () => JSON.stringify(body),
      } as Response;
    });

    const pdp = createGovernancePdp({
      baseUrl: 'https://example.com',
      token: 'tok',
      agentKey: 'sales-agent',
      environment: 'staging',
      verifyKey: SECRET,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    const refreshed = await pdp.refresh();
    expect(refreshed.packVersion).toBe('pack-v1');
    expect(refreshed.state).toBe('normal');

    const result = pdp.evaluate({
      agent_key: 'sales-agent',
      tool_name: 'calendar.get_events',
    });
    expect(result.decision).toBe('allow');
  });

  it('rejects tampered bundle on refresh', async () => {
    const pack = makePack();
    const bundle = makeBundle();
    bundle.payload = { ...bundle.payload, version: 'evil' };

    const fetchImpl = jest.fn(async (url: string) => {
      const body = url.includes('/packs/')
        ? { success: true, data: pack }
        : { success: true, data: bundle };
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        json: async () => body,
        text: async () => JSON.stringify(body),
      } as Response;
    });

    const pdp = createGovernancePdp({
      baseUrl: 'https://example.com',
      token: 'tok',
      agentKey: 'sales-agent',
      verifyKey: SECRET,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    await expect(pdp.refresh()).rejects.toThrow(/signature/i);
  });

  it('keeps last-known bundle when refresh fails, then fail-closes past grace', async () => {
    const pack = makePack();
    let clock = Date.now();
    const bundle = makeBundle({
      expires_at: new Date(clock + 5_000).toISOString(),
      grace_ms: 5_000,
    });

    let call = 0;
    const fetchImpl = jest.fn(async (url: string) => {
      call += 1;
      if (call > 2) {
        return {
          ok: false,
          status: 503,
          statusText: 'Unavailable',
          json: async () => ({ success: false }),
          text: async () => 'down',
        } as Response;
      }
      const body = url.includes('/packs/')
        ? { success: true, data: pack }
        : { success: true, data: bundle };
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        json: async () => body,
        text: async () => JSON.stringify(body),
      } as Response;
    });

    const pdp = createGovernancePdp({
      baseUrl: 'https://example.com',
      token: 'tok',
      agentKey: 'sales-agent',
      verifyKey: SECRET,
      fetchImpl: fetchImpl as unknown as typeof fetch,
      now: () => clock,
    });

    await pdp.refresh();
    expect(pdp.evaluate({ agent_key: 'sales-agent', tool_name: 'calendar.get_events' }).decision).toBe(
      'allow'
    );

    // Second refresh fails — still within TTL → cached
    const second = await pdp.refresh();
    expect(second.state).toBe('cached');

    // Past grace → fail_closed deny
    clock += 20_000;
    const denied = pdp.evaluate({
      agent_key: 'sales-agent',
      tool_name: 'calendar.get_events',
    });
    expect(denied.decision).toBe('deny');
    expect(denied.pdp_state).toBe('fail_closed');
  });
});

describe('createGovernancePdp bundle fetch outcome', () => {
  function respond(status: number, body: unknown): Response {
    return {
      ok: status >= 200 && status < 300,
      status,
      statusText: String(status),
      json: async () => body,
      text: async () => JSON.stringify(body),
    } as Response;
  }

  function pdpWith(route: (url: string) => Response | Promise<Response>) {
    const fetchImpl = jest.fn(async (url: string, _init?: RequestInit) => route(url));
    const pdp = createGovernancePdp({
      baseUrl: 'https://example.com',
      token: 'tok',
      agentKey: 'sales-agent',
      verifyKey: SECRET,
      headers: { 'x-promptforge-pep-role': 'service' },
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    return { pdp, fetchImpl };
  }

  it('is null before the first refresh', () => {
    const { pdp } = pdpWith(() => respond(200, {}));
    expect(pdp.getLastBundleFetch()).toBeNull();
  });

  it('tells "no policy" (404) apart from an outage', async () => {
    const missing = pdpWith((url) =>
      url.includes('/bundles/')
        ? respond(404, { success: false, error: 'No policy bundle' })
        : respond(200, { success: true, data: makePack() })
    );
    await expect(missing.pdp.refresh()).rejects.toThrow(/404/);
    expect(missing.pdp.getLastBundleFetch()).toBe('not_found');

    const down = pdpWith(() => respond(503, { success: false }));
    await expect(down.pdp.refresh()).rejects.toThrow(/503/);
    expect(down.pdp.getLastBundleFetch()).toBe('failed');

    const offline = pdpWith(() => {
      throw new Error('ECONNREFUSED');
    });
    await expect(offline.pdp.refresh()).rejects.toThrow(/ECONNREFUSED/);
    expect(offline.pdp.getLastBundleFetch()).toBe('failed');
  });

  it('records a bundle that fails verification as unverified', async () => {
    const bundle = makeBundle();
    bundle.payload = { ...bundle.payload, version: 'evil' };
    const { pdp } = pdpWith((url) =>
      respond(200, { success: true, data: url.includes('/packs/') ? makePack() : bundle })
    );
    await expect(pdp.refresh()).rejects.toThrow(/signature/i);
    expect(pdp.getLastBundleFetch()).toBe('unverified');
  });

  it('loads the bundle when the pack is missing', async () => {
    const { pdp } = pdpWith((url) =>
      url.includes('/packs/')
        ? respond(404, { success: false })
        : respond(200, { success: true, data: makeBundle() })
    );
    const refreshed = await pdp.refresh();
    expect(refreshed.packVersion).toBe('unknown');
    expect(refreshed.state).toBe('normal');
    expect(pdp.getLastBundleFetch()).toBe('ok');
    expect(pdp.getContentPack()).toBeNull();
    expect(
      pdp.evaluate({ agent_key: 'sales-agent', tool_name: 'calendar.get_events' }).decision
    ).toBe('allow');
  });

  it('sends the caller headers without letting them replace Authorization', async () => {
    const { pdp, fetchImpl } = pdpWith((url) =>
      respond(200, { success: true, data: url.includes('/packs/') ? makePack() : makeBundle() })
    );
    await pdp.refresh();
    const headers = fetchImpl.mock.calls[0][1]?.headers as Record<string, string>;
    expect(headers['x-promptforge-pep-role']).toBe('service');
    expect(headers.Authorization).toBe('Bearer tok');
  });
});
