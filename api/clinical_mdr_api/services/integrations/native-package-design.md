# Reviewed clinical design in Package V2.2

The existing checkpoint reader calls `USDMService.get_by_uid_with_report` with
the checkpoint's exact native study UID and value version. The package retains
the complete document, explicit version/design selection, mapping findings and
native source records as `OsbCanonicalStudyDefinitionV1@1.0.0`. Missing clinical
values remain missing and mapper findings remain visible.

`OsbPackageNativeStateV1@1.1.0` includes the canonical definition hash. Specialist
review commits to that state and hash; package generation rereads the same native
version and rejects any post-review change. Review and package preparation both
perform the existing final state reread. A historical review of the smaller state
cannot authorize this expanded package; obtain a fresh review through the existing
workflow. Failed native export does not fall back to a minimal document.

New packages use `OsbNativePackageV2@2.2.0`. The schema retains 2.0/2.1 for history;
updated CSL and EDC consumers explicitly accept 2.2. Study standards remain bound
to the supplied platform manifest. The design's original USDM identity is not
replaced by the platform identity. Its native UID/version and mapper report bind
that document to the platform's locked study.

CSL verifies the review commitment, and EDC imports the full clinical document as
a draft using the existing study exchange and source-custody machinery. Native
release, application and human activation remain separate. This does not make
unsupported native capture mappings executable or establish clinical approval.
