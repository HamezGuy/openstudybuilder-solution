# pylint: disable=logging-fstring-interpolation
import argparse
import csv

import requests

from .functions.parsers import map_boolean
from .functions.utils import load_env
from .utils.importer import BaseImporter, open_file
from .utils.metrics import Metrics
from .utils.path_join import path_join

# ---------------------------------------------------------------
# Env loading
# ---------------------------------------------------------------
#
API_BASE_URL = load_env("API_BASE_URL")


class FeatureFlags(BaseImporter):
    logging_name = "feature_flags"

    def __init__(self, api=None, metrics_inst=None):
        super().__init__(api=api, metrics_inst=metrics_inst)

    @open_file()
    def handle_feature_flags(self, csvfile, update: bool = False):
        feature_flags_in_db = requests.get(
            path_join(self.api.api_base_url, "/feature-flags"),
            headers=self.api.api_headers,
        ).json()
        feature_flags_in_db = {item["name"]: item for item in feature_flags_in_db}

        csv_data = csv.DictReader(csvfile)
        feature_flags_in_csv = {item["name"]: item for item in csv_data}

        for feature_flag_name, feature_flag_data in feature_flags_in_csv.items():
            body = {
                "section": feature_flag_data["section"],
                "feature": feature_flag_data["feature"],
                "name": feature_flag_data["name"],
                "enabled": map_boolean(feature_flag_data["enabled"]),
                "description": feature_flag_data["description"] or None,
            }

            if update and feature_flag_data["name"] in feature_flags_in_db:
                _old = feature_flags_in_db[feature_flag_data["name"]]
                _sn = str(_old["sn"])
                del _old["sn"]

                if _old == body:
                    self.log.info(
                        f"Feature flag '{feature_flag_data['name']}' already exists with provided values {body}"
                    )
                    continue

                self.log.info(
                    f"Update feature flag '{feature_flag_data['name']}' from {_old} to {body}"
                )

                body["uid"] = _sn
                self.api.patch_to_api(body=body, path="feature-flags")
            elif not update and feature_flag_data["name"] in feature_flags_in_db:
                self.log.info(
                    f"Skipping. Feature flag '{feature_flag_data['name']}' already exists."
                )
            else:
                data = {
                    "path": "feature-flags",
                    "body": body,
                }

                self.log.info(f"Add feature flag '{data['body']['name']}'")

                self.api.post_to_api(data)

        if update:
            for feature_flag_name, feature_flag_data in feature_flags_in_db.items():
                if feature_flag_name not in feature_flags_in_csv:
                    self.log.info(f"Delete feature flag '{feature_flag_name}'")
                    self.api.delete_to_api(f"feature-flags/{feature_flag_data['sn']}")

    def run(self, update: bool = False):
        feature_flags = load_env("FEATURE_FLAGS")
        self.log.info("Importing feature flags")

        self.handle_feature_flags(feature_flags, update=update)

        self.log.info("Done importing feature flags")


def main(update: bool = False):
    metr = Metrics()
    migrator = FeatureFlags(metrics_inst=metr)
    migrator.run(update=update)
    metr.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--update",
        action="store_true",
        help="Whether to update existing feature flags or not.",
    )
    args = parser.parse_args()

    main(update=args.update)
