"""Read/write bridge to the 360i `ecrf_platform` Postgres database.

The 360i pipeline (EDCProtocolToECRF) persists an OSB-shaped projection of
every study build to `osb_study_payloads` (its migration 033), and this
importer records what it did to `osb_import_ledger` (034). OSB itself is
Neo4j-only, so THIS module is the only place in the OSB estate that speaks
Postgres — it must never leak into clinical-mdr-api.

Tenancy: every table involved has FORCEd row-level security keyed on the
`app.tenant_id` GUC, so each connection sets it immediately after connect.
A wrong or missing tenant does not error — it sees zero rows — which is why
`read_latest_payload` distinguishes "no payload" loudly.

Auth: connect with a least-privilege role (`osb_importer`: SELECT on
osb_study_payloads, SELECT+INSERT on osb_import_ledger). Dev falls back to
whatever ECRF_PG_DSN carries (typically app_rw).

Env:
    ECRF_PG_DSN     e.g. postgresql://osb_importer:...@localhost:5442/ecrf_platform
    ECRF_TENANT_ID  the 360i tenant whose payloads to read (RLS scope)
"""

import gzip
import json
import uuid

import psycopg

from ..functions.utils import load_env

IMPORTER_VERSION = "360i-importer/1.0"


class EcrfPlatformDb:
    """One connection to ecrf_platform, tenant-scoped for its whole life."""

    def __init__(self, dsn=None, tenant_id=None, log=None):
        self.dsn = dsn or load_env("ECRF_PG_DSN")
        self.tenant_id = tenant_id or load_env("ECRF_TENANT_ID")
        self.log = log
        self.conn = psycopg.connect(self.dsn)
        # RLS scope for every statement on this connection. set_config with
        # is_local=false pins it for the session, not one transaction.
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.tenant_id', %s, false)", (self.tenant_id,)
            )
        self.conn.commit()

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    # ------------------------------------------------------------------
    # osb_study_payloads (read)
    # ------------------------------------------------------------------

    def read_latest_payload(self, study_id):
        """The study's newest payload row, parsed, or None when it has none.

        Returns a dict: {payload_hash, study_id, build_hash, prior_payload_hash,
        format_version, census columns..., payload} where payload is the parsed
        Osb360iPayloadV1.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT payload_hash, study_id, build_hash, prior_payload_hash,
                       format_version, census_total, census_mapped, census_carried,
                       census_out_of_scope, census_unmapped,
                       payload_jsonb, payload_gzip
                  FROM osb_study_payloads
                 WHERE study_id = %s
                 ORDER BY created_at DESC, payload_hash
                 LIMIT 1
                """,
                (study_id,),
            )
            row = cur.fetchone()
        return self._row_to_payload(row)

    def read_payload(self, payload_hash):
        """One payload by hash, parsed, or None."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT payload_hash, study_id, build_hash, prior_payload_hash,
                       format_version, census_total, census_mapped, census_carried,
                       census_out_of_scope, census_unmapped,
                       payload_jsonb, payload_gzip
                  FROM osb_study_payloads
                 WHERE payload_hash = %s
                """,
                (payload_hash,),
            )
            row = cur.fetchone()
        return self._row_to_payload(row)

    def list_payload_studies(self):
        """Every study with at least one payload: [(study_id, latest created_at)]."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT study_id, MAX(created_at)
                  FROM osb_study_payloads
                 GROUP BY study_id
                 ORDER BY MAX(created_at) DESC
                """
            )
            return cur.fetchall()

    @staticmethod
    def _row_to_payload(row):
        if row is None:
            return None
        (
            payload_hash,
            study_id,
            build_hash,
            prior_payload_hash,
            format_version,
            census_total,
            census_mapped,
            census_carried,
            census_out_of_scope,
            census_unmapped,
            payload_jsonb,
            payload_gzip,
        ) = row
        if payload_gzip is not None:
            payload = json.loads(gzip.decompress(bytes(payload_gzip)).decode("utf-8"))
        elif isinstance(payload_jsonb, (dict, list)):
            payload = payload_jsonb
        else:
            payload = json.loads(payload_jsonb)
        return {
            "payload_hash": payload_hash,
            "study_id": study_id,
            "build_hash": build_hash,
            "prior_payload_hash": prior_payload_hash,
            "format_version": format_version,
            "census": {
                "total": census_total,
                "mapped": census_mapped,
                "carried": census_carried,
                "declaredOutOfScope": census_out_of_scope,
                "unmapped": census_unmapped,
            },
            "payload": payload,
        }

    # ------------------------------------------------------------------
    # osb_import_ledger (read the crosswalk back, write attempts)
    # ------------------------------------------------------------------

    def read_current_crosswalk(self, study_id):
        """The latest non-failed ledger row for a study — the current
        360i-study -> OSB-study mapping — or None when never imported.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT import_id, payload_hash, osb_study_uid, osb_project_number,
                       status, census, uid_map, imported_at
                  FROM osb_import_ledger
                 WHERE study_id = %s AND status IN ('succeeded', 'partial')
                       AND osb_study_uid IS NOT NULL
                 ORDER BY imported_at DESC
                 LIMIT 1
                """,
                (study_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "import_id": str(row[0]),
            "payload_hash": row[1],
            "osb_study_uid": row[2],
            "osb_project_number": row[3],
            "status": row[4],
            "census": row[5],
            "uid_map": row[6],
            "imported_at": row[7],
        }

    def write_import_ledger(
        self,
        study_id,
        payload_hash,
        osb_study_uid,
        osb_project_number,
        status,
        census,
        uid_map,
    ):
        """Append one import-attempt row. `partial` requires >=1 stopped entry
        in the census — asserted here so a dishonest status cannot land.
        """
        if status == "partial" and not census.get("stopped"):
            raise ValueError(
                "status='partial' requires the census to name what stopped"
            )
        import_id = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO osb_import_ledger (
                    tenant_id, import_id, study_id, payload_hash, osb_study_uid,
                    osb_project_number, status, census, uid_map, importer_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    self.tenant_id,
                    import_id,
                    study_id,
                    payload_hash,
                    osb_study_uid,
                    osb_project_number,
                    status,
                    json.dumps(census),
                    json.dumps(uid_map),
                    IMPORTER_VERSION,
                ),
            )
        self.conn.commit()
        return import_id
