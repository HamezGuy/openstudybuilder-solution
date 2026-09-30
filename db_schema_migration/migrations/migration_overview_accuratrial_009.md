# Platform domain-audit integrity

Adds scoped node-key constraints for OSB command and domain-audit history and installs an APOC `before` trigger that rejects property mutation, property removal, label removal, or deletion of published command effects, audit events, outbox results, signed audit roots, and export records. New nodes may be created only through their owning transactional publication paths.

The migration fails rather than inventing or repairing signed audit history when an existing audit/root/export node lacks its canonical payload, hash, or attestation fields.
