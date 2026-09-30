> **AccuraTrial fork migrations.** The fork's own schema migrations run in a
> separate series so they never collide with upstream's numbering. They were
> first committed as `migration_024.py` … `migration_032.py` and renamed on
> 2026-09-30 when upstream 2.10 shipped its own `migration_024.py`:
>
> | Now | Was | `MIGRATION_DESC` |
> |---|---|---|
> | `migration_accuratrial_001` | `migration_024` | `osb-proposal-v2-review-constraints` |
> | `migration_accuratrial_002` | `migration_025` | `osb-proposal-v2-execution-authorization-constraint` |
> | `migration_accuratrial_003` | `migration_026` | `osb-native-study-tenant-scope` |
> | `migration_accuratrial_004` | `migration_027` | `osb-native-study-identity-publication` |
> | `migration_accuratrial_005` | `migration_028` | `osb-platform-command-publication` |
> | `migration_accuratrial_006` | `migration_029` | `osb-candidate-request-and-set-v1` |
> | `migration_accuratrial_007` | `migration_030` | `osb-study-mapping-decision-native-evidence-v1` |
> | `migration_accuratrial_008` | `migration_031` | `osb-native-package-v2-prototype` |
> | `migration_accuratrial_009` | `migration_032` | `osb-platform-domain-audit-integrity` |
>
> The descriptions are unchanged, so a database that already ran a migration
> under its old file name records the same migration. Each one only adds
> constraints or guards for fork-owned labels and runs independently of the
> upstream series: `python -m migrations.migration_accuratrial_00N`.

# Governed Proposal V2 review boundary

## Indexes and constraints

Adds node-key constraints for immutable/content-addressed integration records:

- `OsbMappingContextSnapshot.context_hash`
- `OsbProposalReview.proposal_hash`
- `OsbProposalReviewObject.object_key`
- `OsbProposalReviewDecision.decision_id`

These constraints make concurrent context/proposal intake idempotent at the Neo4j
boundary rather than relying on application-level `MERGE` timing.