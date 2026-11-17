# Document source push notifications

Push notifications request a sync of an existing document source. They never
write document content or change the source's access policy. The normal
15-minute poll remains enabled and is the recovery path for missed deliveries.
Push setup, callbacks and renewal are off by default; an operator enables
`LOOM_KNOWLEDGE_SOURCE_PUSH_ENABLED` for the deployment. Disabling it pauses
deliveries and renewal without preventing secret resealing or setup retirement.

## Registration and authentication

The push registry is separate from the source registry and sync cursors. A row
binds an org, namespace, source ID, provider and random setup generation to a
fingerprint of the source's credential and items. Configuration is an admin
operation. An explicitly configured HTTPS public base URL is required.

The callback URL names the org, source ID and setup generation. A callback
re-reads the registry and current source under the namespace's source lease
before recording a sync request. A removed or replaced source, changed
credential, changed item set, inactive namespace or retired generation refuses
the delivery. Callback payloads and headers are not stored or logged.

- Drive channels use independent random channel IDs and sealed channel tokens.
  A delivery must match the active channel ID, token and resource ID before its
  expiration. This is channel-token verification: Drive does not sign these
  deliveries. Early `sync` notifications are acknowledged without requesting
  work. Watch responses are activated only after rechecking the setup generation.
- Notion setup captures one bounded, sealed verification challenge for a fresh
  generation. Only an admin can reveal it while setup is pending. After verifying
  the subscription in the provider's UI, the admin confirms that exact generation
  and challenge fingerprint. An unsigned challenge cannot activate a subscription
  or request sync. Active deliveries require the raw-body HMAC and the configured
  workspace identity.
- Linear setup first exposes a pending notification address. The admin creates
  its webhook there, then confirms the same generation with its signing secret.
  Deliveries require the raw-body HMAC, the signed body's millisecond timestamp
  within one minute and the configured organization identity.

Provider identity is read using the source's existing credential during setup.
Notification bodies have a fixed size limit. Duplicate valid notifications
coalesce with the existing pending sync request; a waiting full sync is preserved.
Successful callbacks answer HTTP 200 within a four-second handler budget.

## Drive coverage and renewal

A Drive user change channel does not cover every shared-drive change. Setup
therefore creates channels for the user corpus and every shared drive the account
belongs to, up to 19 drives. A complete, bounded membership listing avoids mistaking
a directly shared file's drive ID for membership: a nonmember's directly shared
items use the user log. Setup refuses an incomplete or over-limit corpus set
rather than claiming complete push coverage. Moving a file
to a new corpus may require setup again; polling still discovers the change.

A bounded, default-off worker renews channels before expiration. It creates a
new unique channel, validates the response and source generation, then swaps it
under the source lease. It stops the old channel best effort. A failed renewal
leaves polling available and exposes the expired channel in setup status.

## Lifecycle and storage

The registry stores the full identity tuple, server-stamped actor and derived
provenance. Secrets use purpose-bound sealing and participate in resealing.
Ordinary source reads and audit subjects never include tokens or challenges.
The admin setup read bypasses idempotency replay storage, so a revealed pending
challenge cannot be replayed from an ordinary cache after activation.

Source removal, credential rotation, item updates and namespace retirement
retire push setup under the same source lease. Late external watch responses
cannot revive retired setup. Namespace erasure removes every registry version
through the existing settled erasure machinery. Configuration writes require a
fleet that understands the registry's erasure step; an unreadable fleet refuses.
Before rolling back to a build without this registry, disable push, erase the
registry rows with the current build and verify the namespace erasures complete.
Provider channels that cannot be stopped expire; retired callbacks refuse them.

## Alternatives

Putting push state in the source row would make every delivery contend with
credential writes and risk carrying an old credential forward. Putting it in
sync detail would mix sealed setup secrets with ordinary inspection output.
Both are avoided by the separate registry. Notifications are hints, so a durable
event queue, provider payload archive and a second ingestion path are unnecessary.

## Provider contracts

- [Drive push notifications](https://developers.google.com/workspace/drive/api/guides/push)
- [Drive user and shared-drive logs](https://developers.google.com/workspace/drive/api/guides/about-changes)
- [Notion webhook verification](https://developers.notion.com/reference/webhooks)
- [Notion event delivery](https://developers.notion.com/reference/webhooks-events-delivery)
- [Linear webhook verification](https://linear.app/developers/webhooks)
