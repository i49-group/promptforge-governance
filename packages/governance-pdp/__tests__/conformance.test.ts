import { readFileSync } from 'fs';
import { join } from 'path';
import { evaluateAgainstBundle } from '../src/evaluate';
import type { PdpState, PolicyBundlePayload, SignedPolicyBundle } from '../src/types';

/**
 * Runs conformance/vectors.json against the reference evaluator.
 *
 * The same file is run by hermes-plugin/test_conformance.py. If the two ever
 * disagree, one of them is wrong and hosts are being governed differently — the
 * situation this suite exists to make impossible.
 */

interface Vector {
  id: string;
  why: string;
  bundle: string;
  pdp_state: PdpState;
  tool_name: string;
  expect: {
    decision: string;
    tier: string;
    requires_approval: boolean;
    reasons: string[];
  };
}

interface VectorFile {
  schema_version: number;
  bundles: Record<string, PolicyBundlePayload>;
  cases: Vector[];
}

const vectorPath = join(__dirname, '../../../conformance/vectors.json');
const vectors: VectorFile = JSON.parse(readFileSync(vectorPath, 'utf8'));

function bundleFor(name: string): SignedPolicyBundle {
  const payload = vectors.bundles[name];
  if (!payload) throw new Error(`vectors.json references unknown bundle "${name}"`);
  return {
    bundle_id: `conformance-${name}`,
    alg: 'HS256',
    key_id: 'conformance',
    // Signature verification is out of scope here; evaluateAgainstBundle takes
    // an already-verified bundle.
    signature: 'not-verified-in-conformance',
    payload,
  };
}

describe('conformance vectors', () => {
  it('declares the schema version this runner understands', () => {
    expect(vectors.schema_version).toBe(1);
  });

  it('has no duplicate case ids', () => {
    const ids = vectors.cases.map((c) => c.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  test.each(vectors.cases.map((c) => [c.id, c] as const))('%s', (_id, vector) => {
    const result = evaluateAgainstBundle(
      bundleFor(vector.bundle),
      { agent_key: 'conformance', tool_name: vector.tool_name },
      vector.pdp_state
    );

    expect({
      decision: result.decision,
      tier: result.tier,
      requires_approval: result.requires_approval,
      reasons: result.reasons,
      category: result.category,
    }).toEqual(vector.expect);
  });
});
