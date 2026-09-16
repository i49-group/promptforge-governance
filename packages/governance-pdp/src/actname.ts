/**
 * Act-name canonicalization. Reference implementation.
 *
 * Policies are *written* in a dotted `domain.action` form. Hosts *emit* MCP acts as
 * `mcp__<server>__<domain>_<action>`. Nothing reconciled the two, so every dotted entry in
 * every policy was unreachable, and category-level rules — which key on the dot — never
 * resolved at all.
 *
 * The canonical form is the **dotted** one, and that is structural rather than a
 * preference: the category mechanism is defined on `domain.action`, so if the prefixed
 * spelling were canonical there would be no dot to derive a category from. The emitted
 * name is therefore normalised onto the stored one, never the reverse.
 *
 * Two rules govern how this is used, both load-bearing:
 *
 * - **Exact match always wins.** A policy that names the emitted spelling keeps resolving
 *   to that entry, so canonicalization cannot change an answer that already resolves. It
 *   can only reach entries that were previously unreachable.
 * - **Server identity is discarded deliberately.** `mcp__example_server__email_send_now`
 *   and the same act behind a differently-named server canonicalize alike. That is the
 *   point — a policy should not break because a host renamed an MCP server — and it is
 *   also a real limitation: if two servers expose the same `domain_action` pair, one
 *   policy entry governs both. Use the exact prefixed spelling when they must differ.
 *
 * `hermes-plugin/actname.py` must stay line-for-line equivalent to this file, and
 * `conformance/vectors.json` proves that it does.
 */

/**
 * `mcp__<server>__<rest>`. The server segment may itself contain single underscores
 * (`brilliant_central`), so the separator matched is the DOUBLE underscore.
 */
const MCP_PREFIX = /^mcp__([a-z0-9]+(?:_[a-z0-9]+)*)__(.+)$/i;

/**
 * A single-underscore variant appears in older host output
 * (`mcp_example_server_calendar_create_event`). It is genuinely ambiguous — nothing
 * distinguishes the server segment from the domain segment — so it is NOT canonicalized.
 */
const MCP_SINGLE = /^mcp_(?!_)[a-z0-9_]+$/i;

/**
 * True for single-underscore MCP spellings, which cannot be canonicalized safely.
 *
 * `mcp_example_server_calendar_create_event` could split as server `example` / domain
 * `server`, or server `example_server` / domain `calendar`, and there is no way to tell
 * from the string. Such a name should keep denying as an unknown act — loudly — rather
 * than be mapped by a guess onto a policy entry that may grant something else entirely.
 */
export function isAmbiguousMcpName(act: string): boolean {
  return MCP_SINGLE.test(act) && !MCP_PREFIX.test(act);
}

/**
 * The dotted canonical form of an emitted act name, or null when there is nothing to
 * canonicalize (a native act, an already-dotted act, or an ambiguous spelling).
 *
 * `mcp__example_server__email_send_now` → `email.send_now`
 * `terminal` → null (native acts have no domain/action shape and need none)
 * `email.send_now` → null (already canonical)
 */
export function canonicalAct(act: string): string | null {
  if (!act || act.includes('.')) return null;

  const match = MCP_PREFIX.exec(act);
  if (!match) return null;

  const rest = match[2];
  // First underscore-separated token is the domain; everything after it is the action.
  // A rest with no underscore (`email`) has no action, so there is no dotted form.
  const cut = rest.indexOf('_');
  if (cut <= 0 || cut === rest.length - 1) return null;
  return `${rest.slice(0, cut)}.${rest.slice(cut + 1)}`;
}

/**
 * Policy keys to try, in precedence order. The emitted name first — so an exact entry
 * always wins and no resolving act changes answer — then the canonical dotted form.
 */
export function resolutionCandidates(act: string): string[] {
  const candidates = [act];
  const canon = canonicalAct(act);
  if (canon && canon !== act) candidates.push(canon);
  return candidates;
}
