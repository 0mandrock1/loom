# Cost budgets

Namespace and named-user caps use the rolling 24-hour cost ledger in `agent_events`.
A positive namespace policy overrides its corresponding server default; zero inherits,
and zero on both tiers disables that cap. A named user's cap is independent of the
namespace ceiling. Admission refuses paid synthesis when either is reached, and model
routing uses the larger spend fraction. `memory.ask` supplies the same logical user it
records in the ledger. Namespace-wide workers keep their existing namespace accounting;
a token subject is never substituted for an unattributed user. These soft controls do
not reserve spend or establish a billing identity.

`cost.budget_status` shares the policy and spend read with the guards. User mode requires
a namespace, reports that user's counters and also exposes `namespace_over_cap`.
Transport admission enforces a token's pinned user. Namespace and org modes retain their
existing read scopes. The [cost guide](../features/observability/cost-and-usage.md#daily-spend-caps)
describes configuration and warning timing.

An optional `budget_alert_inbox` must name an existing inbox owned by the budget's
namespace in the same org. Registration validates it and delivery rechecks ownership.
Point checks and status polls schedule bounded background warnings through the existing
task event stream, with at most 32 pending deliveries per process. Deliveries for one
org run serially within the process so a namespace and user warning do not compete
with each other. There is no discovery worker. The event ID includes org, namespace, inbox, selected user, cap, warning share
and UTC day. A distributed claim and strongly consistent event read serialize the
check and insert; a deduplication token plus readback handles an uncertain retry.
Notification failures cannot change a cap verdict.

Every delivery and task-event erasure takes the same org-wide warning-writer lease.
Inserts use the existing server-side start deadline and execution bound; the lease TTL
exceeds the working window plus the full send bound. An uncertain insert or cancellation
leaves that lease to expire, so erasure cannot acquire it until every possible send has
finished. Erasure keeps its tagged mutation, polling and resume protocol. Ledger checks
before and after publication invalidate an observation reached by erasure; post-write
cleanup names only that delivery's exact event and sequence. An unreadable ledger
refuses publication.

Warning rows carry the full identity tuple, a process actor and derived source class.
User erasure deletes their task events and the residue census verifies the deletion;
namespace erasure covers both tasks and events. Added columns have defaults for older
writers. Warning production waits for every live replica to support the migration, so
an older erasure implementation cannot miss user-bearing warning rows during a rolling
upgrade. Unknown fleet state defers publication until a later check.
