import asyncio
import csv
import time

import aiohttp

from .functions.parsers import map_boolean
from .functions.utils import load_env
from .utils.importer import BaseImporter, open_file_async
from .utils.metrics import Metrics

# ---------------------------------------------------------------
# Env loading
# ---------------------------------------------------------------
#
API_BASE_URL = load_env("API_BASE_URL")
MDR_MIGRATION_RESPONSE_CODELISTS = load_env("MDR_MIGRATION_RESPONSE_CODELISTS")
MDR_MIGRATION_RESPONSE_CODELISTS_LIMIT = int(
    load_env("MDR_MIGRATION_RESPONSE_CODELISTS_LIMIT", default=0)
)

CODELIST_TYPE = "Response"


class ResponseCodelists(BaseImporter):
    logging_name = "response_codelists"

    def __init__(self, api=None, metrics_inst=None):
        super().__init__(api=api, metrics_inst=metrics_inst)

    async def _post_codelist_and_approve(
        self, body: dict, session: aiohttp.ClientSession
    ):
        status, response = await self.api.post_to_api_async(
            url="/ct/codelists", body=body, session=session
        )
        uid = response.get("codelist_uid")
        if uid is not None:
            time.sleep(0.05)
            status, _ = await self.api.approve_async(
                f"/ct/codelists/{uid}/names/approvals", session=session
            )
            if status != 201:
                self.log.error(f"Failed to approve name for codelist '{body['name']}'")
            time.sleep(0.05)
            status, _ = await self.api.approve_async(
                f"/ct/codelists/{uid}/attributes/approvals", session=session
            )
            if status != 201:
                self.log.error(
                    f"Failed to approve attributes for codelist '{body['name']}'"
                )
            return uid
        if status == 400 and "already exists" in response.get("message", ""):
            self.log.info(
                f"Codelist '{body['name']}' already exists, skipping creation"
            )
            return None
        self.log.error(
            f"Failed to create codelist '{body['name']}', response: {response}"
        )
        return None

    async def _process_term(
        self,
        codelist_uid: str,
        term_submval: str | None,
        order_value: str | None,
        is_ordinal: bool,
        term_data: dict,
        session: aiohttp.ClientSession,
    ):
        name = term_data["sponsor_preferred_name"]
        definition = term_data["definition"]

        # Find existing term by submission value in this codelist
        existing_term = self.api.find_term_by_submission_value(
            codelist_uid, term_submval
        )
        if existing_term:
            term_uid = existing_term["term_uid"]
            self.log.info(
                f"Found existing term '{name}' by submission value, uid '{term_uid}'"
            )
        else:
            # Look for a matching sponsor term by name and definition
            existing_term = self.api.find_sponsor_term_by_name_and_definition(
                name, definition
            )
            if existing_term is None:
                self.log.info(f"Creating new term '{name}'")
                term_uid = await self.post_and_approve_term(term_data, session)
                if not term_uid:
                    return
            else:
                term_uid = existing_term["term_uid"]
                self.log.info(
                    f"Found existing term '{name}' by name/definition, uid '{term_uid}'"
                )

        # Parse the order value
        try:
            parsed_order = int(order_value) if order_value else None
        except ValueError:
            parsed_order = None

        # Link term to codelist, using ordinal or order depending on IS_ORDINAL
        link_body = {"term_uid": term_uid, "submission_value": term_submval}
        if is_ordinal:
            link_body["ordinal"] = parsed_order
            link_body["order"] = parsed_order
        else:
            link_body["order"] = parsed_order

        status, result = await self.api.post_to_api_async(
            url=f"/ct/codelists/{codelist_uid}/terms",
            body=link_body,
            session=session,
        )
        if status != 201:
            self.log.error(
                f"Failed to add term '{name}' to codelist '{codelist_uid}': {result}"
            )
        else:
            self.log.info(f"Added term '{name}' to codelist '{codelist_uid}'")

    @open_file_async()
    async def handle_response_codelists(self, csvfile, session):
        rows = list(csv.DictReader(csvfile, delimiter=","))

        # Step 1: collect unique codelists (first row per submission value)
        seen_codelists: dict[str, dict] = {}
        for row in rows:
            submval = row["CODELIST_SUBMVAL"]
            if submval and submval not in seen_codelists:
                seen_codelists[submval] = row
                if 0 < MDR_MIGRATION_RESPONSE_CODELISTS_LIMIT <= len(seen_codelists):
                    self.log.info("Reached codelist creation limit, stopping.")
                    break

        # Step 2: create codelists sequentially so UIDs are available for terms
        codelist_uids: dict[str, str] = {}
        codelist_is_ordinal: dict[str, bool] = {}
        for submval, row in seen_codelists.items():
            try:
                is_ordinal = map_boolean(
                    row.get("IS_ORDINAL") or "N", raise_exception=True
                )
            except ValueError:
                is_ordinal = False

            codelist_name = row["CODELIST_NAME"]
            body = {
                "catalogue_names": ["SDTM CT"],
                "name": codelist_name,
                "submission_value": submval,
                "nci_preferred_name": "TBD",
                "definition": row.get("CODELIST_DEFINITION") or "TBD",
                "extensible": True,
                "is_ordinal": is_ordinal,
                "sponsor_preferred_name": codelist_name,
                "template_parameter": False,
                "library_name": "Sponsor",
                "codelist_type": CODELIST_TYPE,
                "terms": [],
            }
            self.log.info(f"Creating response codelist '{codelist_name}' ({submval})")
            uid = await self._post_codelist_and_approve(body, session)
            if uid is None:
                # Codelist may already exist — look it up so terms can be linked
                uid = self.api.get_codelist_uid(submval)
            if uid:
                codelist_uids[submval] = uid
                codelist_is_ordinal[submval] = is_ordinal

        # Step 3: create terms in parallel
        api_tasks = []
        for row in rows:
            submval = row["CODELIST_SUBMVAL"]
            if submval not in seen_codelists:
                continue  # Skip rows for codelists that were not created due to limit
            codelist_uid = codelist_uids.get(submval)
            if not codelist_uid:
                self.log.warning(
                    f"Codelist '{submval}' not found, skipping term '{row.get('SPONSOR_NAME')}'"
                )
                continue

            name = row.get("SPONSOR_NAME")
            if not name or not row.get("TERM_DEFINITION"):
                self.log.warning(
                    f"Term row missing SPONSOR_NAME or TERM_DEFINITION, skipping: {row}"
                )
                continue

            term_data = {
                "concept_id": None,
                "catalogue_names": [],
                "nci_preferred_name": "UNK",
                "definition": row.get("TERM_DEFINITION"),
                "sponsor_preferred_name": name,
                "sponsor_preferred_name_sentence_case": name.lower(),
                "library_name": "Sponsor",
            }
            self.log.info(f"Adding term '{name}' to response codelist '{submval}'")
            api_tasks.append(
                self._process_term(
                    codelist_uid=codelist_uid,
                    term_submval=row.get("TERM_SUBMVAL"),
                    order_value=row.get("ORDER"),
                    is_ordinal=codelist_is_ordinal[submval],
                    term_data=term_data,
                    session=session,
                )
            )

        await asyncio.gather(*api_tasks)

    async def async_run(self):
        timeout = aiohttp.ClientTimeout(None)
        conn = aiohttp.TCPConnector(limit=4, force_close=True)
        async with aiohttp.ClientSession(timeout=timeout, connector=conn) as session:
            await self.handle_response_codelists(
                MDR_MIGRATION_RESPONSE_CODELISTS, session
            )

    def run(self):
        self.log.info("Importing response codelists")
        asyncio.run(self.async_run())
        self.log.info("Done importing response codelists")


def main():
    metr = Metrics()
    migrator = ResponseCodelists(metrics_inst=metr)
    migrator.run()
    metr.print()


if __name__ == "__main__":
    main()
