import asyncio
import csv
import sys
import time

import aiohttp

from .functions.parsers import map_boolean
from .functions.utils import load_env
from .utils.importer import BaseImporter, open_file_async
from .utils.metrics import Metrics
from .utils.path_join import path_join

# ---------------------------------------------------------------
# Env loading
# ---------------------------------------------------------------
#
SAMPLE = load_env("MDR_MIGRATION_SAMPLE", default="False") == "True"
API_BASE_URL = load_env("API_BASE_URL")
MDR_MIGRATION_SPONSOR_CODELIST_DEFINITIONS = load_env(
    "MDR_MIGRATION_SPONSOR_CODELIST_DEFINITIONS"
)
MDR_MIGRATION_CODELIST_PARAMETER_SET = load_env("MDR_MIGRATION_CODELIST_PARAMETER_SET")


# TODO rename to codelist definitions
class StandardCodelistTerms1(BaseImporter):
    logging_name = "standard_codelistterms1"

    def __init__(self, api=None, metrics_inst=None):
        super().__init__(api=api, metrics_inst=metrics_inst)
        self.limit_to_codelists = None

    def limit_codelists(self, codelists):
        self.limit_to_codelists = codelists

    @open_file_async()
    async def handle_codelist_definitions(self, csvfile, session):
        # General handler for creating codelists in libraries.
        read_csv = csv.DictReader(csvfile, delimiter=",")
        all_records = []
        stashed_records = {}
        added_submvals = set()
        # Loop through the records and sort them to ensure that any record that has a "codes_submission_value"
        # is processed after the record that has this submission_value.
        for row in read_csv:
            submval = row["submission_value"]
            codes_submval = row["codes_submission_value"]
            if codes_submval and codes_submval not in added_submvals:
                # This codelist has a paired codes codelist that needs to be processd before this one
                stashed_records[codes_submval] = row
            else:
                # There is no codes_submission_value, or the paired list has already been added.
                all_records.append(row)
                added_submvals.add(submval)
            if submval in stashed_records:
                all_records.append(stashed_records[submval])
                added_submvals.add(submval)
                del stashed_records[submval]

        api_tasks = []
        for row in all_records:
            new_codelist_name = row["new_codelist_name"]
            catalogue_name = row["catalogue"]
            if (
                self.limit_to_codelists
                and new_codelist_name not in self.limit_to_codelists
            ):
                self.log.info(f"Skipping codelist definition '{new_codelist_name}'")
                continue
            try:
                extensible = map_boolean(row["extensible"], raise_exception=True)
            except ValueError as e:
                self.log.warning(
                    f"Error parsing 'extensible' value {row['extensible']} as boolean \nerror: {e}\nDefaulting to False"
                )
                extensible = False
            try:
                template_parameter = map_boolean(
                    row["template_parameter"], raise_exception=True
                )
            except ValueError as e:
                self.log.warning(
                    f"Error parsing 'template_parameter' value {row['template_parameter']} as boolean \nerror: {e}\nDefaulting to False"
                )
                template_parameter = False
            codes_submission_value = row.get("codes_submission_value", None)
            if codes_submission_value:
                paired_codelist_uid = self.api.get_codelist_uid(codes_submission_value)
            else:
                paired_codelist_uid = None

            data = {
                "path": "/ct/codelists",
                "body": {
                    "catalogue_names": [catalogue_name],
                    "name": new_codelist_name,
                    "submission_value": row["submission_value"],
                    "nci_preferred_name": row["preferred_term"] or "TBD",
                    "definition": row["definition"] or "TBD",
                    "extensible": extensible,
                    "is_ordinal": row["ordinal"],
                    "sponsor_preferred_name": row["new_codelist_name"],
                    "template_parameter": template_parameter,
                    "library_name": row["library"],
                    "terms": [],
                    "paired_codes_codelist_uid": paired_codelist_uid,
                },
            }
            # TODO Add check if we already have the code list
            self.log.info(
                f"Adding codelist name '{new_codelist_name}' to library '{data['body']['library_name']}'"
            )
            api_tasks.append(
                self.post_codelist_approve_name_or_attribute(data=data, session=session)
            )
        await asyncio.gather(*api_tasks)

    @open_file_async()
    async def handle_codelist_parameter_set(self, csvfile, session):
        # Mark codelist parameters as a template parameters
        readCSV = csv.reader(csvfile, delimiter=",")
        headers = next(readCSV)
        api_tasks = []

        for row in readCSV:
            url = path_join(
                "/ct/codelists", row[headers.index("CODELIST_CONCEPT_ID")], "names"
            )
            change_description = f"Marking {row[headers.index('DESCRIPTION')]} as TemplateParameter in the migration"
            data = {
                "get_path": url,
                "path": path_join(url, "versions"),
                "patch_path": url,
                "approve_path": path_join(url, "approvals"),
                "body": {
                    "template_parameter": True,
                    "change_description": change_description,
                },
            }
            # TODO check if already exists
            self.log.info(
                f"Adding codelist parameter set '{row[headers.index('CODELIST_CONCEPT_ID')]}' with description '{row[headers.index('DESCRIPTION')]}'"
            )
            api_tasks.append(
                self.process_codelist_parameter(data=data, session=session)
            )
        await asyncio.gather(*api_tasks)

    ############ helper functions ###########
    async def post_codelist_approve_name_or_attribute(
        self, data: dict, session: aiohttp.ClientSession
    ):
        status, response = await self.api.post_to_api_async(
            url=data["path"], body=data["body"], session=session
        )
        uid = response.get("codelist_uid")
        if uid != None:
            # Give the backend a little time before approving, otherwise approve may fail.
            # Seems to be a problem only when running locally.
            # We do all these in parallel, this sleep should not affect the time it takes to run the import.
            time.sleep(0.05)
            status, result = await self.api.approve_async(
                f"/ct/codelists/{uid}/names/approvals", session=session
            )
            if status != 201:
                self.log.error(
                    f"Failed to approve name for codelist: {data['body']['name']}"
                )
                self.metrics.icrement("/ct/codelists/-NamesApproveError")
            else:
                self.log.info(f"Approved name for codelist: {data['body']['name']}")
                self.metrics.icrement("/ct/codelists/-NamesApprove")
            time.sleep(0.05)
            status, result = await self.api.approve_async(
                f"/ct/codelists/{uid}/attributes/approvals", session=session
            )
            if status != 201:
                self.log.error(
                    f"Failed to approve attributes for codelist: {data['body']['name']}"
                )
                self.metrics.icrement("/ct/codelists/-AttributesApproveError")
            else:
                self.log.info(
                    f"Approved attributes for codelist: {data['body']['name']}"
                )
                self.metrics.icrement("/ct/codelists/-AttributesApprove")
            return result
        elif status == 400 and "already exists" in response.get("message", ""):
            self.log.info(
                f"Codelist {data['body']['name']} already exists, skipping approve."
            )
            return response
        else:
            self.log.error(
                f"Failed to create codelist: {data['body']['name']}, response: {response}"
            )
            self.metrics.icrement("/ct/codelists-ERROR")
            return response

    async def process_codelist_parameter(
        self, data: dict, session: aiohttp.ClientSession
    ):
        get_result = {}
        async with session.get(
            path_join(API_BASE_URL, data["get_path"]), headers=self.api.api_headers
        ) as response:
            status = response.status
            get_result = await response.json()
        if get_result is not None and get_result.get("template_parameter") is True:
            self.metrics.icrement("/ct/codelists-AlreadyIsTemplateParameter")
            return get_result
        _status, _post_result = await self.api.post_to_api_async(
            url=data["path"], body={}, session=session
        )
        _patch_status, _patch_result = await self.api.patch_to_api_async(
            path=data["patch_path"], body=data["body"], session=session
        )
        status, result = await self.api.approve_async(
            data["approve_path"], session=session
        )
        if status != 201:
            self.metrics.icrement("/ct/codelists/-NamesApproveError")
        else:
            self.metrics.icrement("/ct/codelists/-NamesApprove")
        return result

    async def async_run(self):
        timeout = aiohttp.ClientTimeout(None)
        conn = aiohttp.TCPConnector(limit=4, force_close=True)
        async with aiohttp.ClientSession(timeout=timeout, connector=conn) as session:
            await self.handle_codelist_definitions(
                MDR_MIGRATION_SPONSOR_CODELIST_DEFINITIONS, session
            )
            await self.handle_codelist_parameter_set(
                MDR_MIGRATION_CODELIST_PARAMETER_SET, session
            )

    def run(self):
        self.log.info("Importing standard codelists")
        asyncio.run(self.async_run())
        self.log.info("Done importing standard codelists")


def main(codelists=None):
    metr = Metrics()
    migrator = StandardCodelistTerms1(metrics_inst=metr)
    if codelists:
        migrator.limit_codelists(codelists)
    migrator.run()
    metr.print()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        codelist_arg = sys.argv[1]
        codelists = codelist_arg.split(",")
    else:
        codelists = []
    main(codelists=codelists)
