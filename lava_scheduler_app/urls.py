# -*- coding: utf-8 -*-
# Copyright (C) 2011-2019 Linaro Limited
#
# Author: Neil Williams <neil.williams@linaro.org>
#         Remi Duraffort <remi.duraffort@linaro.org>
#
# This file is part of LAVA.
#
# LAVA is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License version 3
# as published by the Free Software Foundation
#
# LAVA is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with LAVA.  If not, see <http://www.gnu.org/licenses/>.

from lava_scheduler_app.views import (
    active_device_list,
    active_jobs,
    all_device_types,
    device_detail,
    device_dictionary,
    device_dictionary_plain,
    device_health,
    device_list,
    device_reports,
    device_type_detail,
    device_type_health_history_log,
    device_type_reports,
    download_device_type_template,
    failure_report,
    favorite_jobs,
    health_job_list,
    healthcheck,
    index,
    internal_v1_jobs,
    internal_v1_jobs_logs,
    internal_v1_workers,
    job_annotate_failure,
    job_cancel,
    job_change_priority,
    job_configuration,
    job_definition,
    job_definition_plain,
    job_description_yaml,
    job_detail,
    job_errors,
    job_fail,
    job_fetch_data,
    job_list,
    job_log_file_plain,
    job_log_incremental,
    job_resubmit,
    job_status,
    job_submit,
    job_timing,
    job_toggle_favorite,
    lab_health,
    longest_jobs,
    maintenance_devices,
    multinode_job_definition,
    multinode_job_definition_plain,
    my_active_jobs,
    my_error_jobs,
    my_queued_jobs,
    mydevice_list,
    mydevices_health_history_log,
    myjobs,
    online_device_list,
    passing_health_checks,
    queue,
    reports,
    running,
    similar_jobs,
    username_list_json,
    worker_detail,
    worker_health,
    workers,
)
from lava_server.compat import url

urlpatterns = [
    url(r"^$", index, name="lava.scheduler"),
    url(r"^reports$", reports, name="lava.scheduler.reports"),
    url(r"^reports/failures$", failure_report, name="lava.scheduler.failure_report"),
    url(r"^activejobs$", active_jobs, name="lava.scheduler.job.active"),
    url(r"^alljobs$", job_list, name="lava.scheduler.job.list"),
    url(r"joberrors$", job_errors, name="lava.scheduler.job.errors"),
    url(r"^jobsubmit$", job_submit, name="lava.scheduler.job.submit"),
    url(r"^device_types$", all_device_types, name="lava.scheduler.device_types"),
    url(
        r"^device_type/(?P<pk>[-_a-zA-Z0-9]+)$",
        device_type_detail,
        name="lava.scheduler.device_type.detail",
    ),
    url(r"^alldevices$", device_list, name="lava.scheduler.alldevices"),
    url(
        r"^device/(?P<pk>[-_a-zA-Z0-9.@]+)$",
        device_detail,
        name="lava.scheduler.device.detail",
    ),
    url(
        r"^device/(?P<pk>[-_a-zA-Z0-9.@]+)/devicedict$",
        device_dictionary,
        name="lava.scheduler.device.dictionary",
    ),
    url(
        r"^device/(?P<pk>[-_a-zA-Z0-9.@]+)/devicedict/plain$",
        device_dictionary_plain,
        name="lava.scheduler.device.dictionary.plain",
    ),
    url(r"^allworkers$", workers, name="lava.scheduler.workers"),
    url(
        r"^worker/(?P<pk>[-_a-zA-Z0-9.@]+)$",
        worker_detail,
        name="lava.scheduler.worker.detail",
    ),
    url(
        r"^worker/(?P<pk>[-_a-zA-Z0-9.@]+)/health$",
        worker_health,
        name="lava.scheduler.worker.health",
    ),
    url(r"^labhealth/$", lab_health, name="lava.scheduler.labhealth"),
    url(
        r"^labhealth/device/(?P<pk>[-_a-zA-Z0-9.@]+)$",
        health_job_list,
        name="lava.scheduler.labhealth.detail",
    ),
    url(r"^longestjobs$", longest_jobs, name="lava.scheduler.longest_jobs"),
    url(
        r"^job/(?P<pk>[0-9]+|[0-9]+\.[0-9]+)$",
        job_detail,
        name="lava.scheduler.job.detail",
    ),
    url(
        r"^job/(?P<pk>[0-9]+|[0-9]+\.[0-9]+)/definition$",
        job_definition,
        name="lava.scheduler.job.definition",
    ),
    url(
        r"^job/(?P<pk>[0-9]+|[0-9]+\.[0-9]+)/definition/plain$",
        job_definition_plain,
        name="lava.scheduler.job.definition.plain",
    ),
    url(
        r"^job/(?P<pk>[0-9]+|[0-9]+\.[0-9]+)/description$",
        job_description_yaml,
        name="lava.scheduler.job.description.yaml",
    ),
    url(
        r"^job/(?P<pk>[0-9]+|[0-9]+\.[0-9]+)/multinode_definition$",
        multinode_job_definition,
        name="lava.scheduler.job.multinode_definition",
    ),
    url(
        r"^job/(?P<pk>[0-9]+|[0-9]+\.[0-9]+)/multinode_definition/plain$",
        multinode_job_definition_plain,
        name="lava.scheduler.job.multinode_definition.plain",
    ),
    url(
        r"^job/(?P<pk>[0-9]+|[0-9]+\.[0-9]+)/configuration$",
        job_configuration,
        name="lava.scheduler.job.configuration",
    ),
    url(
        r"^job/(?P<pk>[0-9]+|[0-9]+\.[0-9]+)/log_file/plain$",
        job_log_file_plain,
        name="lava.scheduler.job.log_file.plain",
    ),
    url(
        r"^job/(?P<pk>[0-9]+|[0-9]+\.[0-9]+)/timing$",
        job_timing,
        name="lava.scheduler.job.timing",
    ),
    url(
        r"^job/(?P<pk>[0-9]+|[0-9]+\.[0-9]+)/job_status$",
        job_status,
        name="lava.scheduler.job_status",
    ),
    url(
        r"^job/(?P<pk>[0-9]+|[0-9]+\.[0-9]+)/cancel$",
        job_cancel,
        name="lava.scheduler.job.cancel",
    ),
    url(
        r"^job/(?P<pk>[0-9]+|[0-9]+\.[0-9]+)/fail$",
        job_fail,
        name="lava.scheduler.job.fail",
    ),
    url(
        r"^job/(?P<pk>[0-9]+|[0-9]+\.[0-9]+)/resubmit$",
        job_resubmit,
        name="lava.scheduler.job.resubmit",
    ),
    url(
        r"^job/(?P<pk>[0-9]+|[0-9]+\.[0-9]+)/annotate_failure$",
        job_annotate_failure,
        name="lava.scheduler.job.annotate_failure",
    ),
    url(
        r"^job/(?P<pk>[0-9]+|[0-9]+\.[0-9]+)/toggle_favorite$",
        job_toggle_favorite,
        name="lava.scheduler.job.toggle_favorite",
    ),
    url(
        r"^job/(?P<pk>[0-9]+|[0-9]+\.[0-9]+)/log_pipeline_incremental$",
        job_log_incremental,
        name="lava.scheduler.job.log_incremental",
    ),
    url(
        r"^job/(?P<pk>[0-9]+|[0-9]+\.[0-9]+)/job_data$",
        job_fetch_data,
        name="lava.scheduler.job.fetch_data",
    ),
    url(r"^myjobs$", myjobs, name="lava.scheduler.myjobs"),
    url(r"^myactivejobs$", my_active_jobs, name="lava.scheduler.myjobs.active"),
    url(r"^myqueuedjobs$", my_queued_jobs, name="lava.scheduler.myjobs.queued"),
    url(r"^myerrorjobs$", my_error_jobs, name="lava.scheduler.myjobs.error"),
    url(r"^favorite-jobs$", favorite_jobs, name="lava.scheduler.favorite_jobs"),
    url(
        r"^job/(?P<pk>[0-9]+|[0-9]+\.[0-9]+)/priority$",
        job_change_priority,
        name="lava.scheduler.job.priority",
    ),
    url(
        r"^device/(?P<pk>[-_a-zA-Z0-9.@]+)/health$",
        device_health,
        name="lava.scheduler.device.health",
    ),
    url(
        r"^alldevices/active$", active_device_list, name="lava.scheduler.active_devices"
    ),
    url(
        r"^alldevices/online$", online_device_list, name="lava.scheduler.online_devices"
    ),
    url(
        r"^alldevices/passinghealthchecks$",
        passing_health_checks,
        name="lava.scheduler.passing_health_checks",
    ),
    url(
        r"^alldevices/maintenance$",
        maintenance_devices,
        name="lava.scheduler.maintenance_devices",
    ),
    url(
        r"^reports/device/(?P<pk>[-_a-zA-Z0-9.@]+)",
        device_reports,
        name="lava.scheduler.device_report",
    ),
    url(
        r"^reports/device_type/(?P<pk>[-_a-zA-Z0-9]+)",
        device_type_reports,
        name="lava.scheduler.device_type_report",
    ),
    url(r"^mydevices$", mydevice_list, name="lava.scheduler.mydevice_list"),
    url(
        r"^username-list-json$",
        username_list_json,
        name="lava.scheduler.username_list_json",
    ),
    url(r"^queue$", queue, name="lava.scheduler.queue"),
    url(r"^healthcheck$", healthcheck, name="lava.scheduler.healthcheck"),
    url(r"^running$", running, name="lava.scheduler.running"),
    url(
        r"^dthealthhistory/device_type/(?P<pk>[-_a-zA-Z0-9]+)",
        device_type_health_history_log,
        name="lava.scheduler.device_type_health_history_log",
    ),
    url(
        r"^mydevicetypehealthhistory$",
        mydevices_health_history_log,
        name="lava.scheduler.mydevices_health_history_log",
    ),
    url(
        r"^devicetypeyaml/(?P<pk>[-_a-zA-Z0-9]+)",
        download_device_type_template,
        name="lava_scheduler_download_device_type_yaml",
    ),
    url(
        r"^job/(?P<pk>[0-9]+|[0-9]+.[0-9]+)/similarjobs$",
        similar_jobs,
        name="lava.scheduler.job.similar_jobs",
    ),
    url(
        r"internal/v1/jobs/(?P<pk>[0-9]+|[0-9]+.[0-9]+)/$",
        internal_v1_jobs,
        name="lava.scheduler.internal.v1.jobs",
    ),
    url(
        r"internal/v1/jobs/(?P<pk>[0-9]+|[0-9]+.[0-9]+)/logs/$",
        internal_v1_jobs_logs,
        name="lava.scheduler.internal.v1.jobs.logs",
    ),
    url(
        r"internal/v1/workers/$",
        internal_v1_workers,
        name="lava.scheduler.internal.v1.workers",
    ),
    url(
        r"internal/v1/workers/(?P<pk>[-_a-zA-Z0-9.@]+)/$",
        internal_v1_workers,
        name="lava.scheduler.internal.v1.workers",
    ),
]
