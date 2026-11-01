"""Who owns a record. Dependency-free so every layer can import it.

An owner is an opaque string: a Cognito user's `sub` once sign-in is configured, or ANONYMOUS when it is not.
Records, locks and applied remediations are all keyed by owner, so one person's investigations, approvals and
feedback are invisible to and unaffected by another's.

ANONYMOUS can never collide with a real owner (a `sub` is a UUID, which the verifier enforces). When sign-in is
configured, writes need a real owner, so the ANONYMOUS bucket stays empty and a route that forgets to scope a
query shows nothing instead of someone else's data.
"""

ANONYMOUS_OWNER = "anonymous"
