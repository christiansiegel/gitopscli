import re
import sys
import unittest
from contextlib import contextmanager
from io import StringIO
import pytest
import os
import shutil
import uuid
from unittest.mock import patch, MagicMock, Mock, call
from gitopscli.commands.deploy import deploy_command


@contextmanager
def captured_output():
    new_out = StringIO()
    old_out = sys.stdout
    try:
        sys.stdout = new_out
        yield sys.stdout
    finally:
        sys.stdout = old_out


class DeployCommandTest(unittest.TestCase):
    def setUp(self):
        def add_patch(target):
            new_mock = patch(target)
            new_mock.start()
            self.addCleanup(new_mock.stop)
            return new_mock

        self.os_path_isfile_mock = add_patch("gitopscli.commands.deploy.os.path.isfile")
        self.update_yaml_file_mock = add_patch("gitopscli.commands.deploy.update_yaml_file")
        create_git_mock = add_patch("gitopscli.commands.deploy.create_git")
        self.create_tmp_dir_mock = add_patch("gitopscli.commands.deploy.create_tmp_dir")
        self.delete_tmp_dir_mock = add_patch("gitopscli.commands.deploy.delete_tmp_dir")

        self.git_util_mock = MagicMock()
        create_git_mock.return_value = self.git_util_mock

    def test_happy_flow(self):
        self.manager = Mock()
        self.manager.attach_mock(self.create_tmp_dir_mock, "create_tmp_dir")
        self.manager.attach_mock(self.git_util_mock, "git_util")
        self.manager.attach_mock(self.delete_tmp_dir_mock, "delete_tmp_dir")
        self.manager.attach_mock(self.update_yaml_file_mock, "update_yaml_file")
        self.manager.attach_mock(self.os_path_isfile_mock, "os.path.isfile")
        # mock git util

        self.git_util_mock.get_full_file_path.return_value = "/tmp/full-file-path.yml"

        self.create_tmp_dir_mock.return_value = "/tmp/created-tmp-dir"

        self.os_path_isfile_mock.return_value = True

        deploy_command(
            command="deploy",
            file="test/file.yml",
            values={"a.b.c": "foo", "a.b.d": "bar"},
            username="USERNAME",
            password="PASSWORD",
            git_user="GIT_USER",
            git_email="GIT_EMAIL",
            create_pr=False,
            auto_merge=False,
            single_commit=False,
            organisation="ORGA",
            repository_name="REPO",
            git_provider="github",
            git_provider_url=None,
        )

        # assert mock calls
        expected_calls = [
            call.create_tmp_dir(),
            call.git_util.checkout("master"),
            call.git_util.get_full_file_path("test/file.yml"),
            call.os.path.isfile("/tmp/full-file-path.yml"),
            call.update_yaml_file("/tmp/full-file-path.yml", "a.b.c", "foo"),
            call.update_yaml_file().__bool__(),
            call.git_util.commit("changed 'a.b.c' to 'foo' in test/file.yml"),
            call.update_yaml_file("/tmp/full-file-path.yml", "a.b.d", "bar"),
            call.update_yaml_file().__bool__(),
            call.git_util.commit("changed 'a.b.d' to 'bar' in test/file.yml"),
            call.git_util.push("master"),
            call.delete_tmp_dir("/tmp/created-tmp-dir"),
        ]
        assert self.manager.mock_calls == expected_calls
