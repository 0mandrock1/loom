// A passive hook resolves existing lineage; it never establishes a parent edge.
import { findProtectionAlias, protectSession, SessionProtectionUnavailable } from "./session-protection.mjs";

function sameRoot(first, second) {
  return first?.base === second?.base && first?.id === second?.id;
}

function refuse(roots) {
  for (const root of roots) {
    try {
      protectSession(root);
    } catch {
      // A failed mark must not leave another known contributor clean.
    }
  }
  throw new SessionProtectionUnavailable();
}

/** Only canonical identity and existing application-owned anchors are authority. */
export function bindHookProtection(payload, { host, event, base, knownRoots = [] } = {}) {
  const roots = [...knownRoots];
  try {
    if (
      !["claude-code", "codex", "cline"].includes(host) ||
      !payload ||
      typeof payload !== "object" ||
      Array.isArray(payload) ||
      typeof payload.session_id !== "string" ||
      !payload.session_id ||
      payload.session_id.length > 1024
    )
      throw new SessionProtectionUnavailable();
    // Resolve this anchor even when another known contributor is unusable.
    const root = findProtectionAlias(host, payload.session_id, { base });
    if (root) roots.push(root);
    if (
      typeof event !== "string" ||
      !event ||
      (payload.hook_event_name !== undefined && payload.hook_event_name !== event)
    ) {
      throw new SessionProtectionUnavailable();
    }
    if (!root || roots.some((anchor) => !sameRoot(anchor, root))) throw new SessionProtectionUnavailable();
    return root;
  } catch {
    return refuse(roots);
  }
}
