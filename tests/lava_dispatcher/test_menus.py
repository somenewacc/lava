# Copyright (C) 2015 Linaro Limited
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


import logging
import re
from unittest.mock import patch

from lava_common.exceptions import JobError
from lava_common.timeout import Timeout
from lava_dispatcher.menus.menus import SelectorMenu
from lava_dispatcher.shell import ShellCommand, ShellSession
from lava_dispatcher.utils.strings import substitute
from tests.lava_dispatcher.test_basic import Factory, StdoutTestCase
from tests.utils import DummyLogger


class TestSelectorMenu(StdoutTestCase):
    def setUp(self):
        super().setUp()
        self.menu = SelectorMenu()
        self.menu.item_markup = (r"\[", r"\]")
        self.menu.item_class = "0-9"
        self.menu.separator = " "
        self.menu.label_class = "a-zA-Z0-9"
        self.menu.prompt = None

    def test_menu_parser(self):
        pattern = "%s([%s]+)%s%s([%s]*)" % (
            re.escape(self.menu.item_markup[0]),
            self.menu.item_class,
            re.escape(self.menu.item_markup[1]),
            self.menu.separator,
            self.menu.label_class,
        )
        serial_input = """
    [1] debian
    [2] tester
    [3] Shell
    [4] Boot Manager
    [5] Reboot
    [6] Shutdown
    Start:
            """
        selection = self.menu.select(serial_input, "Shell")
        self.assertEqual(self.menu.pattern, pattern)
        for line in serial_input.split("\n"):
            match = re.search(pattern, line)
            if match:
                if match.group(2) == "Shell":
                    self.assertEqual(match.group(1), selection)


class MenuFactory(Factory):
    """
    Not Model based, this is not a Django factory.
    Factory objects are dispatcher based classes, independent
    of any database objects.
    """

    def create_uefi_job(self, filename):
        job = super().create_job("mustang-uefi-01.jinja2", filename)
        job.logger = DummyLogger()
        return job


class TestUefi(StdoutTestCase):
    def setUp(self):
        super().setUp()
        factory = MenuFactory()
        self.job = factory.create_uefi_job("sample_jobs/mustang-menu-ramdisk.yaml")

    def test_check_char(self):
        shell = ShellCommand(
            "%s\n" % "ls", Timeout("fake", 30), logger=logging.getLogger()
        )
        if shell.exitstatus:
            raise JobError(
                "%s command exited %d: %s" % ("ls", shell.exitstatus, shell.readlines())
            )
        connection = ShellSession(self.job, shell)
        self.assertFalse(hasattr(shell, "check_char"))
        self.assertTrue(hasattr(connection, "check_char"))
        self.assertIsNotNone(connection.check_char)

    @patch(
        "lava_dispatcher.actions.deploy.tftp.which", return_value="/usr/bin/in.tftpd"
    )
    def test_selector(self, which_mock):
        self.assertIsNotNone(self.job)
        self.job.validate()
        uefi_menu = [
            action
            for action in self.job.pipeline.actions
            if action.name == "uefi-menu-action"
        ][0]
        selector = [
            action
            for action in uefi_menu.pipeline.actions
            if action.name == "uefi-menu-selector"
        ][0]
        params = self.job.device["actions"]["boot"]["methods"]["uefi-menu"][
            "parameters"
        ]
        self.assertEqual(selector.selector.item_markup, params["item_markup"])
        self.assertEqual(selector.selector.item_class, params["item_class"])
        self.assertEqual(selector.selector.separator, params["separator"])
        self.assertEqual(selector.selector.label_class, params["label_class"])
        self.assertEqual(
            selector.selector.prompt, params["bootloader_prompt"]
        )  # initial prompt
        self.assertEqual(selector.boot_message, params["boot_message"])  # final prompt
        self.assertEqual(
            selector.character_delay, self.job.device["character_delays"]["boot"]
        )

    @patch(
        "lava_dispatcher.actions.deploy.tftp.which", return_value="/usr/bin/in.tftpd"
    )
    def test_uefi_job(self, which_mock):
        self.assertIsNotNone(self.job)
        self.job.validate()
        uefi_menu = [
            action
            for action in self.job.pipeline.actions
            if action.name == "uefi-menu-action"
        ][0]
        selector = [
            action
            for action in uefi_menu.pipeline.actions
            if action.name == "uefi-menu-selector"
        ][0]
        self.assertEqual(selector.selector.prompt, "Start:")
        self.assertIsInstance(selector.items, list)
        description_ref = self.pipeline_reference("mustang-uefi.yaml", job=self.job)
        self.assertEqual(description_ref, self.job.pipeline.describe())
        # just dummy strings
        substitution_dictionary = {
            "{SERVER_IP}": "10.4.0.1",
            "{NFS_SERVER_IP}": "10.4.0.2",
            "{RAMDISK}": None,
            "{KERNEL}": "uImage",
            "{DTB}": "mustang.dtb",
            "{NFSROOTFS}": "tmp/tmp21dfed/",
            "{TEST_MENU_NAME}": "LAVA NFS Test Image",
        }
        for block in selector.items:
            if "select" in block:
                if "enter" in block["select"]:
                    block["select"]["enter"] = substitute(
                        [block["select"]["enter"]], substitution_dictionary
                    )
                if "items" in block["select"]:
                    block["select"]["items"] = substitute(
                        block["select"]["items"], substitution_dictionary
                    )
        check_block = [
            {"items": ["Boot Manager"], "wait": "Choice:"},
            {
                "items": ["Remove Boot Device Entry"],
                "fallback": "Return to Main Menu",
                "wait": "Delete entry",
            },
            {"items": ["LAVA NFS Test Image"], "wait": "Choice:"},
            {"items": ["Add Boot Device Entry"], "wait": "Select the Boot Device:"},
            {
                "items": ["TFTP on MAC Address: 00:01:73:69:5A:EF"],
                "wait": "Get the IP address from DHCP:",
            },
            {"enter": ["y"], "wait": "Get the TFTP server IP address:"},
            {
                "enter": ["10.4.0.1"],
                "wait": "File path of the EFI Application or the kernel :",
            },
            {"enter": ["uImage"], "wait": "Is an EFI Application?"},
            {"enter": ["n"], "wait": "Boot Type:"},
            {"enter": ["f"], "wait": "Add an initrd:"},
            {"enter": ["n"], "wait": "Get the IP address from DHCP:"},
            {"enter": ["y"], "wait": "Get the TFTP server IP address:"},
            {"enter": ["10.4.0.1"], "wait": "File path of the FDT :"},
            {"enter": ["mustang.dtb"], "wait": "Arguments to pass to the binary:"},
            {
                "enter": [
                    "console=ttyS0,115200 earlyprintk=uart8250-32bit,0x1c020000 debug root=/dev/nfs rw "
                    "nfsroot=10.4.0.2:tmp/tmp21dfed/,tcp,hard ip=dhcp"
                ],
                "wait": "Description for this new Entry:",
            },
            {"enter": ["LAVA NFS Test Image"], "wait": "Choice:"},
            {"items": ["Return to main menu"], "wait": "Start:"},
            {"items": ["LAVA NFS Test Image"]},
        ]
        for item, check in zip(selector.items, check_block):
            self.assertEqual(item["select"], check)

    @patch(
        "lava_dispatcher.actions.deploy.tftp.which", return_value="/usr/bin/in.tftpd"
    )
    def test_tc2_uefi_job(self, which_mock):
        factory = Factory()
        job = factory.create_job("tc2-01.jinja2", "sample_jobs/tc2.yaml")
        job.validate()
        self.assertEqual([], job.pipeline.errors)
        description_ref = self.pipeline_reference("tc2.yaml", job=self.job)
        self.assertEqual(description_ref, self.job.pipeline.describe())
        self.assertIn("uefi-menu", job.device["actions"]["boot"]["methods"])
        uefi_menu_block = job.device["actions"]["boot"]["methods"]["uefi-menu"]
        nfs_boot = uefi_menu_block["nfs"]
        block = [
            step
            for step in nfs_boot
            if step["select"].get("wait") == "Description for this new Entry:"
        ]
        self.assertIsNotNone(block)
        self.assertEqual(len(block), 1)
        expected_nfs_args = "console=ttyAMA0,38400n8 root=/dev/nfs rw nfsroot={NFS_SERVER_IP}:{NFSROOTFS},tcp,hard,vers=3 rootwait debug systemd.log_target=null user_debug=31 loglevel=9 ip=dhcp"
        self.assertEqual(block[0]["select"]["enter"], expected_nfs_args)
