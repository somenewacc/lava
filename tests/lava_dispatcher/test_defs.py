# Copyright (C) 2014 Linaro Limited
#
# Author: Neil Williams <neil.williams@linaro.org>
#
# This file is part of LAVA Dispatcher.
#
# LAVA Dispatcher is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# LAVA Dispatcher is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along
# with this program; if not, see <http://www.gnu.org/licenses>.

import glob
import os
import re
import shutil
import stat
import subprocess  # nosec - unit test support.
import tempfile
import unittest
from unittest.mock import patch

import pexpect

from lava_common.decorators import nottest
from lava_common.exceptions import InfrastructureError
from lava_common.yaml import yaml_safe_dump, yaml_safe_load
from lava_dispatcher.actions.deploy.download import DownloaderAction
from lava_dispatcher.actions.deploy.image import DeployImagesAction
from lava_dispatcher.actions.deploy.overlay import OverlayAction
from lava_dispatcher.actions.deploy.testdef import (
    GitRepoAction,
    TestDefinitionAction,
    TestInstallAction,
    TestOverlayAction,
    TestRunnerAction,
)
from lava_dispatcher.actions.test.shell import PatternFixup
from lava_dispatcher.parser import JobParser
from lava_dispatcher.power import FinalizeAction
from tests.lava_dispatcher.test_basic import Factory, StdoutTestCase
from tests.lava_dispatcher.test_uboot import UBootFactory
from tests.utils import infrastructure_error, infrastructure_error_multi_paths

# Test the loading of test definitions within the deploy stage


def allow_missing_path(function, testcase, path):
    try:
        function()
    except InfrastructureError as exc:
        if not infrastructure_error(path):
            testcase.fail(exc)


def check_missing_path(testcase, exception, path):
    if isinstance(exception, InfrastructureError):
        if not infrastructure_error(path):
            testcase.fail(exception)


class TestDefinitionHandlers(StdoutTestCase):
    def setUp(self):
        super().setUp()
        self.factory = Factory()
        self.job = self.factory.create_job("qemu01.jinja2", "sample_jobs/kvm.yaml")
        with open(
            os.path.join(os.path.dirname(__file__), "testdefs", "params.yaml"), "r"
        ) as params:
            self.testdef = yaml_safe_load(params)

    def test_testdef(self):
        testdef = overlay = None
        action = self.job.pipeline.actions[0]
        overlay = action.pipeline.actions[0]
        testdef = overlay.pipeline.actions[2]
        self.assertEqual(len(overlay.pipeline.actions), 5)
        self.assertIsInstance(testdef, TestDefinitionAction)
        testdef.validate()
        self.assertEqual(
            testdef.run_levels, {"smoke-tests": 0, "singlenode-advanced": 0}
        )
        if not testdef.valid:
            print(testdef.errors)
        self.assertTrue(testdef.valid)
        for repo_action in testdef.pipeline.actions:
            if isinstance(repo_action, GitRepoAction):
                self.assertTrue(hasattr(repo_action, "accepts"))
                self.assertTrue(hasattr(repo_action, "priority"))
            elif isinstance(repo_action, TestOverlayAction):
                self.assertTrue(hasattr(repo_action, "test_uuid"))
                self.assertFalse(hasattr(repo_action, "accepts"))
                self.assertFalse(hasattr(repo_action, "priority"))
            else:
                self.fail(
                    "%s does not match GitRepoAction or TestOverlayAction"
                    % type(repo_action)
                )
            repo_action.validate()
            # FIXME
            # if hasattr(repo_action, 'uuid'):
            #     repo_action.data['test'] = {repo_action.uuid: {}}
            #     repo_action.store_testdef(self.testdef, 'git', 'abcdef')
            #     self.assertEqual(
            #         repo_action.data['test'][repo_action.uuid]['testdef_pattern'],
            #         self.testdef['parse'])
            self.assertTrue(repo_action.valid)
            # FIXME: needs deployment_data to be visible during validation
            # self.assertNotEqual(repo_action.runner, None)
        self.assertIsNotNone(
            testdef.parameters["deployment_data"]["lava_test_results_dir"]
        )

    def test_name(self):
        deploy = [
            action
            for action in self.job.pipeline.actions
            if action.name == "deployimages"
        ][0]
        overlay = [
            action
            for action in deploy.pipeline.actions
            if action.name == "lava-overlay"
        ][0]
        testdef = [
            action
            for action in overlay.pipeline.actions
            if action.name == "test-definition"
        ][0]
        testdef.validate()
        self.assertEqual([], testdef.errors)
        (rendered, _) = self.factory.create_device("kvm01.jinja2")
        device = yaml_safe_load(rendered)
        kvm_yaml = os.path.join(os.path.dirname(__file__), "sample_jobs/kvm.yaml")
        parser = JobParser()
        with open(kvm_yaml, "r") as sample_job_data:
            content = yaml_safe_load(sample_job_data)
        data = [block["test"] for block in content["actions"] if "test" in block][0]
        definitions = [block for block in data["definitions"] if "path" in block][0]
        definitions["name"] = "smoke tests"
        job = parser.parse(yaml_safe_dump(content), device, 4212, None, "")
        deploy = [
            action for action in job.pipeline.actions if action.name == "deployimages"
        ][0]
        overlay = [
            action
            for action in deploy.pipeline.actions
            if action.name == "lava-overlay"
        ][0]
        testdef = [
            action
            for action in overlay.pipeline.actions
            if action.name == "test-definition"
        ][0]
        testdef.validate()
        self.assertNotEqual([], testdef.errors)
        self.assertIn(
            "Invalid characters found in test definition name: smoke tests",
            job.pipeline.errors,
        )

    def test_vcs_parameters(self):
        deploy = [
            action
            for action in self.job.pipeline.actions
            if action.name == "deployimages"
        ][0]
        overlay = [
            action
            for action in deploy.pipeline.actions
            if action.name == "lava-overlay"
        ][0]
        testdef = [
            action
            for action in overlay.pipeline.actions
            if action.name == "test-definition"
        ][0]
        git_repos = [
            action
            for action in testdef.pipeline.actions
            if action.name == "git-repo-action"
        ]
        for git_repo in git_repos:
            if (
                git_repo.parameters["repository"]
                == "http://git.linaro.org/lava-team/lava-functional-tests.git"
            ):
                self.assertIn("revision", git_repo.parameters)
                self.assertIn("branch", git_repo.parameters)
            else:
                self.assertNotIn("revision", git_repo.parameters)
                self.assertNotIn("branch", git_repo.parameters)

    def test_overlay(self):

        script_list = [
            "lava-add-keys",
            "lava-add-sources",
            "lava-background-process-start",
            "lava-background-process-stop",
            "lava-echo-ipv4",
            "lava-install-packages",
            "lava-installed-packages",
            "lava-os-build",
            "lava-probe-channel",
            "lava-probe-ip",
            "lava-target-ip",
            "lava-target-mac",
            "lava-target-storage",
            "lava-test-case",
            "lava-test-event",
            "lava-test-feedback",
            "lava-test-reference",
            "lava-test-runner",
            "lava-test-set",
            "lava-test-shell",
            "lava-test-raise",
            "lava-common-functions",
        ]

        overlay = None
        action = self.job.pipeline.actions[0]
        for child in action.pipeline.actions:
            if isinstance(child, OverlayAction):
                overlay = child
                break
        self.assertIsInstance(overlay, OverlayAction)
        # Generic scripts
        scripts_to_copy = glob.glob(os.path.join(overlay.lava_test_dir, "lava-*"))
        distro_support_dir = "%s/distro/%s" % (overlay.lava_test_dir, "debian")
        for script in glob.glob(os.path.join(distro_support_dir, "lava-*")):
            scripts_to_copy.append(script)
        check_list = list(set([os.path.basename(scr) for scr in scripts_to_copy]))

        self.assertCountEqual(check_list, script_list)
        self.assertEqual(
            overlay.xmod,
            stat.S_IRWXU | stat.S_IXGRP | stat.S_IRGRP | stat.S_IXOTH | stat.S_IROTH,
        )

    def test_overlay_override(self):
        job = self.factory.create_job("qemu01.jinja2", "sample_jobs/kvm-context.yaml")
        deploy = [
            action for action in job.pipeline.actions if action.name == "deployimages"
        ][0]
        overlay = [
            action
            for action in deploy.pipeline.actions
            if action.name == "lava-overlay"
        ][0]
        self.assertEqual(
            "/sysroot/lava-%s", overlay.get_constant("lava_test_results_dir", "posix")
        )


class TestDefinitionSimple(StdoutTestCase):
    def setUp(self):
        super().setUp()
        factory = Factory()
        self.job = factory.create_kvm_job("sample_jobs/kvm-notest.yaml")

    def test_job_without_tests(self):
        deploy = boot = finalize = None
        allow_missing_path(
            self.job.pipeline.validate_actions, self, "qemu-system-x86_64"
        )
        for action in self.job.pipeline.actions:
            self.assertNotIsInstance(action, TestDefinitionAction)
            self.assertNotIsInstance(action, OverlayAction)
            deploy = self.job.pipeline.actions[0]
            boot = self.job.pipeline.actions[1]
            finalize = self.job.pipeline.actions[2]
        self.assertEqual(deploy.name, "deployimages")
        self.assertEqual(boot.section, "boot")
        self.assertIsInstance(finalize, FinalizeAction)
        self.assertEqual(len(self.job.pipeline.actions), 3)  # deploy, boot, finalize
        self.assertIsInstance(deploy.pipeline.actions[0], DownloaderAction)
        self.assertEqual(
            len(deploy.pipeline.actions), 1
        )  # deploy without test only needs DownloaderAction


class TestDefinitionParams(StdoutTestCase):
    def setUp(self):
        super().setUp()
        self.factory = Factory()
        self.job = self.factory.create_kvm_job("sample_jobs/kvm-params.yaml")

    def test_job_without_tests(self):
        boot = finalize = None
        allow_missing_path(
            self.job.pipeline.validate_actions, self, "qemu-system-x86_64"
        )
        deploy = [
            action
            for action in self.job.pipeline.actions
            if action.name == "deployimages"
        ][0]
        overlay = [
            action
            for action in deploy.pipeline.actions
            if action.name == "lava-overlay"
        ][0]
        testdef = [
            action
            for action in overlay.pipeline.actions
            if action.name == "test-definition"
        ][0]
        for action in self.job.pipeline.actions:
            self.assertNotIsInstance(action, TestDefinitionAction)
            self.assertNotIsInstance(action, OverlayAction)
            boot = self.job.pipeline.actions[1]
            finalize = self.job.pipeline.actions[3]
        self.assertIsInstance(overlay, OverlayAction)
        self.assertIsInstance(testdef, TestDefinitionAction)
        test = testdef.pipeline.actions[1]
        install = testdef.pipeline.actions[2]
        runsh = testdef.pipeline.actions[3]
        self.assertIsInstance(deploy, DeployImagesAction)
        self.assertEqual(boot.section, "boot")
        self.assertIsInstance(finalize, FinalizeAction)
        self.assertEqual(
            len(self.job.pipeline.actions), 4
        )  # deploy, boot, test, finalize
        self.assertNotIn("test_params", testdef.parameters)
        self.assertIsInstance(install, TestInstallAction)
        self.assertIsInstance(runsh, TestRunnerAction)
        self.assertIsNot(list(install.parameters.items()), [])
        testdef = {
            "params": {"VARIABLE_NAME_1": "value_1", "VARIABLE_NAME_2": "value_2"}
        }
        content = test.handle_parameters(testdef)
        self.assertEqual(
            set(content),
            {
                "###default parameters from test definition###\n",
                "VARIABLE_NAME_1='value_1'\n",
                "VARIABLE_NAME_2='value_2'\n",
                "######\n",
                "###test parameters from job submission###\n",
                "VARIABLE_NAME_1='eth2'\n",
                "VARIABLE_NAME_2='wlan0'\n",
                "######\n",
            },
        )
        testdef = {
            "parameters": {"VARIABLE_NAME_1": "value_1", "VARIABLE_NAME_2": "value_2"}
        }
        content = test.handle_parameters(testdef)
        self.assertEqual(
            set(content),
            {
                "###default parameters from test definition###\n",
                "VARIABLE_NAME_1='value_1'\n",
                "VARIABLE_NAME_2='value_2'\n",
                "######\n",
                "###test parameters from job submission###\n",
                "VARIABLE_NAME_1='eth2'\n",
                "VARIABLE_NAME_2='wlan0'\n",
                "######\n",
            },
        )

    @unittest.skipIf(infrastructure_error("git"), "git not installed")
    def test_install_repos(self):
        job = self.factory.create_kvm_job("sample_jobs/kvm-install.yaml")
        allow_missing_path(
            self.job.pipeline.validate_actions, self, "qemu-system-x86_64"
        )
        deploy = [
            action for action in job.pipeline.actions if action.name == "deployimages"
        ][0]
        overlay = [
            action
            for action in deploy.pipeline.actions
            if action.name == "lava-overlay"
        ][0]
        testdef = [
            action
            for action in overlay.pipeline.actions
            if action.name == "test-definition"
        ][0]
        test_install = [
            action
            for action in testdef.pipeline.actions
            if action.name == "test-install-overlay"
        ][0]
        self.assertIsNotNone(test_install)
        yaml_file = os.path.join(os.path.dirname(__file__), "./testdefs/install.yaml")
        self.assertTrue(os.path.exists(yaml_file))
        with open(yaml_file, "r") as test_file:
            testdef = yaml_safe_load(test_file)
        repos = testdef["install"].get("git-repos", [])
        self.assertIsNotNone(repos)
        self.assertIsInstance(repos, list)
        for repo in repos:
            self.assertIsNotNone(repo)
        runner_path = tempfile.mkdtemp()
        test_install.install_git_repos(testdef, runner_path)
        shutil.rmtree(runner_path)


class TestSkipInstall(StdoutTestCase):
    def setUp(self):
        super().setUp()
        factory = UBootFactory()
        self.job = factory.create_bbb_job("sample_jobs/bbb-skip-install.yaml")

    @patch(
        "lava_dispatcher.actions.deploy.tftp.which", return_value="/usr/bin/in.tftpd"
    )
    def test_skip_install_params(self, which_mock):
        self.assertIsNotNone(self.job)
        deploy = [
            action
            for action in self.job.pipeline.actions
            if action.name == "tftp-deploy"
        ][0]
        prepare = [
            action
            for action in deploy.pipeline.actions
            if action.name == "prepare-tftp-overlay"
        ][0]
        lava_apply = [
            action
            for action in prepare.pipeline.actions
            if action.name == "lava-overlay"
        ][0]
        testoverlay = [
            action
            for action in lava_apply.pipeline.actions
            if action.name == "test-definition"
        ][0]
        testdefs = [
            action
            for action in testoverlay.pipeline.actions
            if action.name == "test-install-overlay"
        ]
        ubuntu_testdef = None
        single_testdef = None
        for testdef in testdefs:
            if testdef.parameters["path"] == "lava-test-shell/smoke-tests-basic.yaml":
                ubuntu_testdef = testdef
            elif (
                testdef.parameters["path"]
                == "lava-test-shell/single-node/singlenode03.yaml"
            ):
                single_testdef = testdef
            else:
                self.fail("Unexpected test definition in sample job.")
        self.assertNotIn("skip_install", ubuntu_testdef.parameters)
        self.assertIn("skip_install", single_testdef.parameters)
        self.job.validate()
        self.assertEqual(
            single_testdef.skip_list,
            ["keys", "sources", "deps", "steps", "git-repos", "all"],
        )
        self.assertEqual(single_testdef.skip_options, ["deps"])


@nottest
def check_rpcinfo(server="127.0.0.1"):
    """
    Supports the unittest.SkipIf
    needs to return True if skip, so
    returns True on failure.
    """
    try:
        subprocess.check_output(  # nosec - unit test support.
            ["/usr/sbin/rpcinfo", "-u", server, "nfs", "3"]
        )
    except (OSError, subprocess.CalledProcessError):
        return True
    return False


class TestDefinitions(StdoutTestCase):
    """
    For compatibility until the V1 code is removed and we can start
    cleaning up Lava Test Shell.
    Parsing patterns in the Test Shell Definition YAML are problematic,
    difficult to debug and rely on internal python syntax.
    The fixupdict is even more confusing for all concerned.
    """

    def setUp(self):
        super().setUp()
        self.testdef = os.path.join(
            os.path.dirname(__file__), "testdefs", "params.yaml"
        )
        self.res_data = os.path.join(
            os.path.dirname(__file__), "testdefs", "result-data.txt"
        )
        self.factory = UBootFactory()
        self.job = self.factory.create_bbb_job("sample_jobs/bbb-nfs-url.yaml")

    def test_pattern(self):
        self.assertTrue(os.path.exists(self.testdef))
        with open(self.testdef, "r") as par:
            params = yaml_safe_load(par)
        self.assertIn("parse", params.keys())
        line = "test1a: pass"
        self.assertEqual(
            r"(?P<test_case_id>.*-*):\s+(?P<result>(pass|fail))",
            params["parse"]["pattern"],
        )
        match = re.search(params["parse"]["pattern"], line)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(), line)
        self.assertEqual(match.group(1), "test1a")
        self.assertEqual(match.group(2), "pass")

    def test_v1_defaults(self):
        pattern = PatternFixup(testdef=None, count=0)
        # without a name from a testdef, the pattern is not valid.
        self.assertFalse(pattern.valid())
        with open(self.testdef, "r") as par:
            params = yaml_safe_load(par)
        pattern = PatternFixup(testdef=params, count=0)
        self.assertTrue(pattern.valid())

    @unittest.skipIf(check_rpcinfo(), "rpcinfo returns non-zero for nfs")
    @patch(
        "lava_dispatcher.actions.deploy.tftp.which", return_value="/usr/bin/in.tftpd"
    )
    def test_definition_lists(self, which_mock):
        self.job.validate()
        tftp_deploy = [
            action
            for action in self.job.pipeline.actions
            if action.name == "tftp-deploy"
        ][0]
        prepare = [
            action
            for action in tftp_deploy.pipeline.actions
            if action.name == "prepare-tftp-overlay"
        ][0]
        overlay = [
            action
            for action in prepare.pipeline.actions
            if action.name == "lava-overlay"
        ][0]
        apply_o = [
            action
            for action in prepare.pipeline.actions
            if action.name == "apply-overlay-tftp"
        ][0]
        self.assertIsInstance(apply_o.parameters.get("persistent_nfs"), dict)
        self.assertIsInstance(apply_o.parameters["persistent_nfs"].get("address"), str)
        definition = [
            action
            for action in overlay.pipeline.actions
            if action.name == "test-definition"
        ][0]
        git_repos = [
            action
            for action in definition.pipeline.actions
            if action.name == "git-repo-action"
        ]
        self.assertIn("common", self.job.context)
        self.assertIn("test-definition", self.job.context["common"])
        self.assertIsNotNone(
            definition.get_namespace_data(
                action=definition.name, label="test-definition", key="testdef_index"
            )
        )
        self.assertEqual(
            definition.get_namespace_data(
                action=definition.name, label="test-definition", key="testdef_index"
            ),
            ["smoke-tests", "singlenode-advanced"],
        )
        self.assertEqual(
            git_repos[0].get_namespace_data(
                action="test-runscript-overlay",
                label="test-runscript-overlay",
                key="testdef_levels",
            ),
            {"1.3.2.4.4": "0_smoke-tests", "1.3.2.4.8": "1_singlenode-advanced"},
        )
        self.assertEqual(
            {repo.uuid for repo in git_repos}, {"4999_1.3.2.4.1", "4999_1.3.2.4.5"}
        )
        self.assertEqual(
            set(
                git_repos[0]
                .get_namespace_data(
                    action="test-runscript-overlay",
                    label="test-runscript-overlay",
                    key="testdef_levels",
                )
                .values()
            ),
            {"1_singlenode-advanced", "0_smoke-tests"},
        )
        # fake up a run step
        with open(self.testdef, "r") as par:
            params = yaml_safe_load(par)
        self.assertEqual(
            r"(?P<test_case_id>.*-*):\s+(?P<result>(pass|fail))",
            params["parse"]["pattern"],
        )
        self.job.context.setdefault("test", {})
        for git_repo in git_repos:
            self.job.context["test"].setdefault(git_repo.uuid, {})
            self.job.context["test"][git_repo.uuid]["testdef_pattern"] = {
                "pattern": params["parse"]["pattern"]
            }
        self.assertEqual(
            self.job.context["test"],
            {
                "4999_1.3.2.4.5": {
                    "testdef_pattern": {
                        "pattern": "(?P<test_case_id>.*-*):\\s+(?P<result>(pass|fail))"
                    }
                },
                "4999_1.3.2.4.1": {
                    "testdef_pattern": {
                        "pattern": "(?P<test_case_id>.*-*):\\s+(?P<result>(pass|fail))"
                    }
                },
            },
        )
        testdef_index = self.job.context["common"]["test-definition"][
            "test-definition"
        ]["testdef_index"]
        start_run = "0_smoke-tests"
        uuid_list = definition.get_namespace_data(
            action="repo-action", label="repo-action", key="uuid-list"
        )
        self.assertIsNotNone(uuid_list)
        for key, value in enumerate(testdef_index):
            if start_run == "%s_%s" % (key, value):
                self.assertEqual("4999_1.3.2.4.1", uuid_list[key])
                self.assertEqual(
                    self.job.context["test"][uuid_list[key]]["testdef_pattern"][
                        "pattern"
                    ],
                    "(?P<test_case_id>.*-*):\\s+(?P<result>(pass|fail))",
                )

    def test_defined_pattern(self):
        """
        For python3 support, need to resolve:
        TypeError: cannot use a bytes pattern on a string-like object
        TypeError: cannot use a string pattern on a bytes-like object
        whilst retaining re_pat as a compiled regular expression in the
        pexpect support.
        """
        data = """test1a: pass
test2a: fail
test3a: skip
\"test4a:\" \"unknown\"
        """
        with open(self.testdef, "r") as par:
            params = yaml_safe_load(par)
        pattern = params["parse"]["pattern"]
        re_pat = re.compile(pattern, re.M)
        match = re.search(re_pat, data)
        if match:
            self.assertEqual(
                match.groupdict(), {"test_case_id": "test1a", "result": "pass"}
            )
        child = pexpect.spawn("cat", [self.res_data], encoding="utf-8")
        child.expect([re_pat, pexpect.EOF])
        self.assertEqual(child.after.encode("utf-8"), b"test1a: pass")
        child.expect([re_pat, pexpect.EOF])
        self.assertEqual(child.after.encode("utf-8"), b"test2a: fail")
        child.expect([re_pat, pexpect.EOF])
        self.assertEqual(child.after, pexpect.EOF)

    @unittest.skipIf(
        infrastructure_error_multi_paths(["lxc-info", "img2simg", "simg2img"]),
        "lxc or img2simg or simg2img not installed",
    )
    def test_deployment_data(self):
        job = self.factory.create_job(
            "hi960-hikey-01.jinja2", "sample_jobs/hikey960-oe-aep.yaml"
        )
        job.validate()
        description_ref = self.pipeline_reference("hikey960-oe-aep.yaml", job=job)
        self.assertEqual(description_ref, job.pipeline.describe())

        lxc_deploy = [
            action for action in job.pipeline.actions if action.name == "lxc-deploy"
        ][0]
        lxc_overlay = [
            action
            for action in lxc_deploy.pipeline.actions
            if action.name == "lava-overlay"
        ][0]
        lxc_defs = [
            action
            for action in lxc_overlay.pipeline.actions
            if action.name == "test-definition"
        ][0]
        lxc_installscript = [
            action
            for action in lxc_defs.pipeline.actions
            if action.name == "test-install-overlay"
        ][0]
        fastboot_deploy = [
            action
            for action in job.pipeline.actions
            if action.name == "fastboot-deploy"
        ][0]
        fastboot_overlay = [
            action
            for action in fastboot_deploy.pipeline.actions
            if action.name == "lava-overlay"
        ][0]
        fastboot_defs = [
            action
            for action in fastboot_overlay.pipeline.actions
            if action.name == "test-definition"
        ][0]
        fastboot_installscript = [
            action
            for action in fastboot_defs.pipeline.actions
            if action.name == "test-install-overlay"
        ][0]

        self.assertIn("distro", lxc_installscript.parameters["deployment_data"].keys())
        self.assertIn(
            "distro", list(lxc_installscript.parameters["deployment_data"].keys())
        )
        self.assertIn("distro", list(lxc_installscript.parameters["deployment_data"]))
        self.assertIn("distro", dict(lxc_installscript.parameters["deployment_data"]))
        self.assertEqual(
            "debian", lxc_installscript.parameters["deployment_data"]["distro"]
        )

        self.assertIn(
            "distro", fastboot_installscript.parameters["deployment_data"].keys()
        )
        self.assertIn(
            "distro", list(fastboot_installscript.parameters["deployment_data"].keys())
        )
        self.assertIn("distro", fastboot_installscript.parameters["deployment_data"])
        self.assertIn(
            "distro", dict(fastboot_installscript.parameters["deployment_data"])
        )
        self.assertEqual(
            "oe", fastboot_installscript.parameters["deployment_data"]["distro"]
        )
