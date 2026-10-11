# ADR 0003: Bind publication to reviewed source snapshots

Status: accepted for the S0 local SQLite pilot; production identity/roles remain deferred.
Date: 2026-10-10
Decider: project maintainer through the S0.4.2 implementation directive.

## Context

S0.4.1 records technical PASS/FAIL, but its final reports and lifecycle states could
be assigned directly. A PASS does not establish reuse rights or certify printed values.
Accounts and organization sessions belong to S1, not this administrative data gate.
The MVP must remain operational on its unchanged, separate database.

## Decision

Use an explicit-target local review service with default-deny OS-operator authorization.
Resolve a Windows token SID through the system identity utility (or effective POSIX
UID); require it in the externally configured `SIKA_REVIEWER_IDS` allowlist. Never
accept a reviewer identity from command arguments or review JSON. Do not load `.env`.
This is local operator authorization, not SaaS authentication or tenant authorization.

Migration `0003_publication_gate` protects final quality records, freezes reviewed
cohorts and adds append-only `publication_events`. Events link one release, quality
run and review decision. SQLite triggers require narrow connection-local capabilities
for authoritative evidence and protected transitions. The capability is not a SQL
variable; ordinary SQL clients cannot grant themselves permission. Filesystem/database
owners and arbitrary trusted Python execution remain privileged and outside this threat
boundary. Database access and source archives must therefore retain administrator ACLs.
Review IDs are strictly increasing: caller-chosen lower IDs cannot hide a later veto.
Collision guards cover every protected observation/passage unique key for INSERT and
UPDATE, even when SQLite REPLACE implicit deletes bypass recursive delete triggers.

Approval consumes the newest completed, current-version quality PASS on the exact full
snapshot. It requires verified date/revision marks, allowed reuse with licence evidence
and attribution, source-value evidence for every observation and a reason for every
warning. The service records the reason, resolved principal, QA link and content proof;
it never fabricates review evidence. Publication is a separate transaction consuming
that approval. A later QA run or review decision requires review again.

Full before/after hashes audit each lifecycle transition. A second release-local hash
includes source bytes, all observation facts, referenced catalogs, publisher policy,
passages, originals and migration audit, excluding only release/observation lifecycle
state. This avoids falsely invalidating an approval when an unrelated release changes
state, while detecting every change to the approved cohort. Fresh hard validation and
artifact checks still run before publication. Protected facts cannot be edited in place.

The future query boundary checks published-only states and their retained proof. Changed
metadata, failed newest QA, revoked publishing operator or a later adverse review fail
closed. No current MVP route is switched to this service in S0.4.2.

## Options considered

| Option | Benefit | Cost or limitation |
|---|---|---|
| Service checks only | Smallest implementation | Direct SQL can forge states or reports |
| Full SaaS accounts now | Reusable web identity | Expands beyond S0; sessions and tenant policies are not designed |
| OS operator plus DB guards | Bounded, testable local gate | Requires trusted process/file ACLs; not a remote admin UI |

## Consequences

- Existing S0.4.1 evidence is retained but obsolete for new approvals; revalidate after upgrade.
- Existing unsupported reviewed/published states make upgrade refuse instead of granting trust.
- Downgrade refuses when publication events exist: archive evidence and use a disposable
  restore for rollback rehearsal; never erase publication audit to satisfy a migration.
- PostgreSQL migration SQL renders protection, but authoritative writes fail closed until
  a tested least-privilege service-role adapter is implemented. No PostgreSQL deployment
  is certified by this SQLite pilot.
- Corrections and supersession are not in-place edits. Their future audited workflow
  must preserve existing facts and use new releases/observations.
- Source certification, rights decisions and quarantine triage remain S0.4.3. The real
  corpus is not approved merely because tests demonstrate publication on synthetic data.

## Action items

1. Verify schema guards, stale evidence, authorization, atomicity and protected public reads.
2. Revalidate the local draft corpus without granting review or publication.
3. Complete evidence-based source review in S0.4.3; connect queries only in S0.5.
4. Implement production session/role boundaries and PostgreSQL integration before deployment.
