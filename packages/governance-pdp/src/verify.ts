import { createHmac, timingSafeEqual } from 'crypto';
import { canonicalJson } from './canonical';

export function signPayloadHs256(payload: unknown, secret: string): string {
  const body = canonicalJson(payload);
  return createHmac('sha256', secret).update(body, 'utf8').digest('base64url');
}

export function verifyPayloadHs256(
  payload: unknown,
  signature: string,
  secret: string
): boolean {
  const expected = signPayloadHs256(payload, secret);
  const a = Buffer.from(expected);
  const b = Buffer.from(signature);
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}
