import csv

from .functions.utils import load_env
from .utils.importer import BaseImporter, open_file
from .utils.metrics import Metrics

# ---------------------------------------------------------------
# Env loading
# ---------------------------------------------------------------
#
SAMPLE = load_env("MDR_MIGRATION_SAMPLE", default="False") == "True"
API_BASE_URL = load_env("API_BASE_URL")

MDR_STUDY_FIELDS_DEFINITIONS = load_env("MDR_STUDY_FIELDS_DEFINITIONS")


class Configuration(BaseImporter):
    logging_name = "config"

    @open_file()
    def migrate_study_fields(self, file):
        r = csv.DictReader(file)
        for line in r:
            path = "/configurations"
            data = {idx: None if val == "" else val for idx, val in line.items()}
            # data = camel_case_data(line)
            # TODO check why simple_path="study_fields_configuration"
            self.log.info(
                f"Adding study field '{data['study_field_name']}' to codelist '{data['configured_codelist_name']}'"
            )
            self.api.simple_post_to_api(body=data, path=path, simple_path=path)

    def run(self):
        self.log.info("Importing general config")
        self.migrate_study_fields(MDR_STUDY_FIELDS_DEFINITIONS)
        self.log.info("Done importing general config")


def main():
    metr = Metrics()
    migrator = Configuration(metrics_inst=metr)
    migrator.run()
    metr.print()


if __name__ == "__main__":
    main()
