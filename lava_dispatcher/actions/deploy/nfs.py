# Copyright (C) 2016 Linaro Limited
#
# Author: Dean Arnold <dean.arnold@linaro.org>
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

# List just the subclasses supported for this base strategy
# imported by the parser to populate the list of subclasses.

from lava_dispatcher.action import Action, Pipeline
from lava_dispatcher.actions.deploy.apply_overlay import (
    ApplyOverlayTftp,
    ExtractModules,
    ExtractNfsRootfs,
    OverlayAction,
)
from lava_dispatcher.actions.deploy.download import DownloaderAction
from lava_dispatcher.actions.deploy.environment import DeployDeviceEnvironment
from lava_dispatcher.logical import Deployment


class Nfs(Deployment):
    """
    Strategy class for a NFS deployment.
    Downloads rootfs and deploys to NFS server on dispatcher
    """

    compatibility = 1
    name = "nfs"

    @classmethod
    def action(cls):
        return NfsAction()

    @classmethod
    def accepts(cls, device, parameters):
        if "to" not in parameters:
            return False, '"to" is not in deploy parameters'
        if parameters["to"] != "nfs":
            return False, '"to" parameter is not "nfs"'
        if "image" in device["actions"]["deploy"]["methods"]:
            return False, '"image" was in the device configuration deploy methods'
        if "nfs" in device["actions"]["deploy"]["methods"]:
            return True, "accepted"
        return False, '"nfs" was not in the device configuration deploy methods"'


class NfsAction(Action):

    name = "nfs-deploy"
    description = "deploy nfsrootfs"
    summary = "NFS deployment"

    def validate(self):
        super().validate()
        if not self.valid:
            return
        if "nfsrootfs" in self.parameters and "persistent_nfs" in self.parameters:
            self.errors = "Only one of nfsrootfs or persistent_nfs can be specified"

    def populate(self, parameters):
        download_dir = self.mkdtemp()
        self.pipeline = Pipeline(parent=self, job=self.job, parameters=parameters)
        if "nfsrootfs" in parameters:
            self.pipeline.add_action(
                DownloaderAction(
                    "nfsrootfs", path=download_dir, params=parameters["nfsrootfs"]
                )
            )
        if "modules" in parameters:
            self.pipeline.add_action(
                DownloaderAction(
                    "modules", path=download_dir, params=parameters["modules"]
                )
            )
        # NfsAction is a deployment, so once the nfsrootfs has been deployed, just do the overlay
        self.pipeline.add_action(ExtractNfsRootfs())
        self.pipeline.add_action(OverlayAction())
        self.pipeline.add_action(ExtractModules())
        self.pipeline.add_action(ApplyOverlayTftp())
        if self.test_needs_deployment(parameters):
            self.pipeline.add_action(DeployDeviceEnvironment())
