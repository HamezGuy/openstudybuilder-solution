import unittest
from typing import Collection
from unittest.mock import Mock, patch

from clinical_mdr_api.domain_repositories.models.project import Project
from clinical_mdr_api.domain_repositories.projects.project_repository import (
    ProjectRepository,
)
from clinical_mdr_api.domains.projects.project import ProjectAR
from clinical_mdr_api.tests.unit.domain.utils import random_opt_str, random_str
from common.exceptions import NotFoundException


class MockClinicalProgramme:
    def __init__(self):
        self._uid = random_str()

    @property
    def uid(self):
        return self._uid


def create_random_project_node() -> Project:
    mocked_relationship = Mock()
    mocked_clinical_programme = MockClinicalProgramme()
    mocked_relationship.single = lambda: mocked_clinical_programme
    random_project = Project(
        uid=random_str(),
        project_number=random_str(),
        name=random_str(),
        holds_project=mocked_relationship,
        description=random_opt_str(),
    )
    return random_project


class TestProjectRepositoryImpl(unittest.TestCase):
    @patch(ProjectRepository.__module__ + ".Project")
    def test__project_number_exists_mocked_project_exist(self, project_mock):
        # given
        repo = ProjectRepository()
        project: Project = create_random_project_node()
        project_mock.nodes.first_or_none.return_value = project

        # when
        does_project_number_exist: bool = repo.project_number_exists(
            project.project_number
        )

        # then
        self.assertTrue(does_project_number_exist)

    @patch(ProjectRepository.__module__ + ".Project")
    def test__project_number_exists_mocked_project_not_exist(self, project_mock):
        # given
        repo = ProjectRepository()
        project_mock.nodes.first_or_none.return_value = None

        # when
        does_project_number_exist: bool = repo.project_number_exists(random_str())

        # then
        self.assertFalse(does_project_number_exist)

    @patch(ProjectRepository.__module__ + ".Project")
    def test__find_by_uid_mocked_project_exist(self, project_mock):
        # given
        repo = ProjectRepository()
        project: Project = create_random_project_node()
        project_mock.nodes.get_or_none.return_value = project
        # when
        project_ar: ProjectAR | None = repo.find_by_uid(project.uid)

        # then
        self.assertEqual(project.uid, project_ar.uid)
        self.assertEqual(project.project_number, project_ar.project_number)
        self.assertEqual(project.name, project_ar.name)
        self.assertEqual(
            project.holds_project.single().uid, project_ar.clinical_programme_uid
        )
        self.assertEqual(project.description, project_ar.description)

    @patch(ProjectRepository.__module__ + ".Project")
    def test__find_by_uid_mocked_project_not_exist(self, project_mock):
        # given
        repo = ProjectRepository()
        project_mock.nodes.get_or_none.return_value = None

        # then
        with self.assertRaises(NotFoundException):
            # when
            repo.find_by_uid(random_str())

    @patch(ProjectRepository.__module__ + ".Project")
    def test__find_all_mocked_projects_exist(self, project_mock):
        # given
        repo = ProjectRepository()
        projects: list[Project] = [create_random_project_node() for _ in range(10)]
        project_mock.nodes.all.return_value = projects

        # when
        project_ars: Collection[ProjectAR] = repo.find_all()

        # then
        for project, project_ar in zip(projects, project_ars):
            with self.subTest():
                self.assertEqual(project.uid, project_ar.uid)
                self.assertEqual(project.project_number, project_ar.project_number)
                self.assertEqual(project.name, project_ar.name)
                self.assertEqual(
                    project.holds_project.single().uid,
                    project_ar.clinical_programme_uid,
                )
                self.assertEqual(project.description, project_ar.description)

    @patch(ProjectRepository.__module__ + ".Project")
    def test__find_all_mocked_project_not_exist(self, project_mock):
        # given
        repo = ProjectRepository()
        projects: list[Project] = []
        project_mock.nodes.all.return_value = projects

        # when
        project_ars: Collection[ProjectAR] = repo.find_all()

        # then
        self.assertTrue(len(project_ars) == 0)
