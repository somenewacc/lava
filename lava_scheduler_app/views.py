from collections import defaultdict
import logging
import os
import simplejson
import StringIO
import datetime
import urllib2
from dateutil.relativedelta import relativedelta

from django import forms

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.urlresolvers import reverse
from django.db.models import Count
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotAllowed,
)
from django.shortcuts import (
    get_object_or_404,
    redirect,
    render_to_response,
)
from django.template import RequestContext
from django.template import defaultfilters as filters
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.db import models
from django.db.models import Q

from django_tables2 import Attrs, Column, TemplateColumn

from lava.utils.data_tables.tables import DataTablesTable

from lava_server.views import index as lava_index
from lava_server.bread_crumbs import (
    BreadCrumb,
    BreadCrumbTrail,
)

from lava_scheduler_app.logfile_helper import (
    formatLogFile,
    getDispatcherErrors,
    getDispatcherLogMessages
)
from lava_scheduler_app.models import (
    Device,
    DeviceType,
    DeviceStateTransition,
    TestJob,
    JSONDataError,
    validate_job_json,
    DevicesUnavailableException,
    User,
    Group,
    Worker,
)
from dashboard_app.models import (
    Bundle,
    TestRun,
)


def post_only(func):
    def decorated(request, *args, **kwargs):
        if request.method != 'POST':
            return HttpResponseNotAllowed('Only POST here')
        return func(request, *args, **kwargs)
    return decorated


class DateColumn(Column):

    def __init__(self, **kw):
        self._format = kw.get('date_format', settings.DATETIME_FORMAT)
        super(DateColumn, self).__init__(**kw)

    def render(self, value):
        return filters.date(value, self._format)


def pklink(record):
    job_id = record.pk
    try:
        if record.sub_id:
            job_id = record.sub_id
    except:
        pass
    return mark_safe(
        '<a href="%s">%s</a>' % (
            record.get_absolute_url(),
            escape(job_id)))


class IDLinkColumn(Column):

    def __init__(self, verbose_name="ID", **kw):
        kw['verbose_name'] = verbose_name
        super(IDLinkColumn, self).__init__(**kw)

    def render(self, record):
        return pklink(record)


class RestrictedIDLinkColumn(IDLinkColumn):

    def render(self, record, table):
        if record.is_accessible_by(table.context.get('request').user):
            return pklink(record)
        else:
            return record.pk


def all_jobs_with_custom_sort():
    jobs = TestJob.objects.select_related("actual_device", "requested_device",
                                          "requested_device_type", "submitter", "user", "group")\
        .extra(select={'device_sort': 'coalesce(actual_device_id, '
                                      'requested_device_id, requested_device_type_id)',
                       'duration_sort': 'end_time - start_time'}).all()
    return jobs.order_by('submit_time')


def my_jobs_with_custom_sort(user):
    jobs = TestJob.objects.select_related("actual_device", "requested_device",
                                          "requested_device_type", "group")\
        .extra(select={'device_sort': 'coalesce(actual_device_id, '
                                      'requested_device_id, requested_device_type_id)',
                       'duration_sort': 'end_time - start_time'}).all()\
        .filter(submitter=user)
    return jobs.order_by('submit_time')


class JobTable(DataTablesTable):

    def render_device(self, record):
        if record.actual_device:
            return pklink(record.actual_device)
        elif record.requested_device:
            return pklink(record.requested_device)
        else:
            return mark_safe(
                '<i>' + escape(record.requested_device_type.pk) + '</i>')

    def render_description(self, value):
        if value:
            return value
        else:
            return ''

    sub_id = RestrictedIDLinkColumn(accessor='id')
    status = Column()
    priority = Column()
    device = Column(accessor='device_sort')
    description = Column(attrs=Attrs(width="30%"))
    submitter = Column()
    submit_time = DateColumn()
    end_time = DateColumn()
    duration = Column(accessor='duration_sort')

    datatable_opts = {
        'aaSorting': [[6, 'desc']],
    }
    searchable_columns = ['description']


class IndexJobTable(JobTable):
    def get_queryset(self):
        return all_jobs_with_custom_sort()\
            .filter(status__in=[TestJob.SUBMITTED, TestJob.RUNNING])

    class Meta:
        exclude = ('end_time',)

    datatable_opts = JobTable.datatable_opts.copy()

    datatable_opts.update({
        'iDisplayLength': 25,
    })


def index_active_jobs_json(request):
    return IndexJobTable.json(request)


class ExpandedStatusColumn(Column):

    def __init__(self, verbose_name="Expanded Status", **kw):
        kw['verbose_name'] = verbose_name
        super(ExpandedStatusColumn, self).__init__(**kw)

    def render(self, record):
        if record.status == Device.RUNNING:
            return mark_safe("Running job #%s - %s submitted by %s" % (
                             pklink(record.current_job),
                             record.current_job.description,
                             record.current_job.submitter))
        else:
            return Device.STATUS_CHOICES[record.status][1]


class RestrictedDeviceColumn(Column):

    def __init__(self, verbose_name="Restrictions", **kw):
        kw['verbose_name'] = verbose_name
        super(RestrictedDeviceColumn, self).__init__(**kw)

    def render(self, record):
        label = None
        if record.user:
            label = record.user.email
        if record.group:
            label = "all users in %s group" % record.group
        if record.is_public:
            message = "Unrestricted usage" \
                if label is None else "Unrestricted usage. Device owned by %s." % label
            return message
        return "Job submissions restricted to %s" % label


class DeviceTable(DataTablesTable):

    def get_queryset(self, user=None):
        return Device.objects.select_related("device_type")

    def render_device_type(self, record):
            return pklink(record.device_type)

    hostname = TemplateColumn('''<a href="{{ record.get_absolute_url }}">{{ record.hostname }}</a>
        ''')
    worker_host = Column()
    device_type = Column()
    status = ExpandedStatusColumn("status")
    owner = RestrictedDeviceColumn()
    health_status = Column()

    searchable_columns = ['hostname']

    datatable_opts = {
        'aaSorting': [[0, 'asc']],
        "iDisplayLength": 50
    }


def index_devices_json(request):
    return DeviceTable.json(request)


class WorkerTable(DataTablesTable):

    def get_queryset(self):
        return Worker.objects.all()

    hostname = TemplateColumn('''
    {% if record.heartbeat %}
    <img src="{{ STATIC_URL }}lava_scheduler_app/images/dut-available-icon.png"
          alt="{{ record.heartbeat }}" />
    {% else %}
    <img src="{{ STATIC_URL }}lava_scheduler_app/images/dut-offline-icon.png"
          alt="{{ record.heartbeat }}" />
    {% endif %}&nbsp;&nbsp;
    <a href="{{ record.get_absolute_url }}">{{ record.hostname }}</a>
        ''')
    uptime = Column()
    arch = Column()
    platform = Column()

    searchable_columns = ['hostname']

    datatable_opts = {
        'aaSorting': [[0, 'asc']],
        "iDisplayLength": 50
    }


class WorkerDeviceTable(DeviceTable):

    def get_queryset(self, worker):
        return Device.objects.filter(worker_host=worker)

    datatable_opts = {
        'aaSorting': [[2, 'asc']],
        "iDisplayLength": 50
    }


def worker_device_json(request, pk):
    worker = get_object_or_404(Worker, pk=pk)
    return WorkerDeviceTable.json(request, params=(worker,))


def index_worker_json(request):
    return WorkerTable.json(request)


def health_jobs_in_hr(hr=-24):
    return TestJob.objects.filter(health_check=True,
                                  start_time__gte=(datetime.datetime.now() +
                                                   relativedelta(hours=hr)))\
        .exclude(status__in=[TestJob.SUBMITTED, TestJob.RUNNING])


def _online_total():
    """ returns a tuple of (num_online, num_not_retired) """
    r = Device.objects.all().values('status').annotate(count=Count('status'))
    offline = total = 0
    for res in r:
        if res['status'] in [Device.OFFLINE, Device.OFFLINING]:
            offline += res['count']
        if res['status'] != Device.RETIRED:
            total += res['count']

    return total - offline, total


@BreadCrumb("Scheduler", parent=lava_index)
def index(request):
    return render_to_response(
        "lava_scheduler_app/index.html",
        {
            'device_status': "%d/%d" % _online_total(),
            'health_check_status': "%s/%s" % (
                health_jobs_in_hr().filter(status=TestJob.COMPLETE).count(),
                health_jobs_in_hr().count()),
            'device_type_table': DeviceTypeTable('devicetype', reverse(device_type_json)),
            'devices_table': DeviceTable('devices', reverse(index_devices_json)),
            'worker_table': WorkerTable('worker', reverse(index_worker_json)),
            'active_jobs_table': IndexJobTable(
                'active_jobs', reverse(index_active_jobs_json)),
            'bread_crumb_trail': BreadCrumbTrail.leading_to(index),
            'context_help': BreadCrumbTrail.leading_to(index),
        },
        RequestContext(request))


def type_report_data(start_day, end_day, dt, health_check):
    now = datetime.datetime.now()
    start_date = now + datetime.timedelta(start_day)
    end_date = now + datetime.timedelta(end_day)

    res = TestJob.objects.filter(actual_device__in=Device.objects.filter(device_type=dt),
                                 health_check=health_check,
                                 start_time__range=(start_date, end_date),
                                 status__in=(TestJob.COMPLETE, TestJob.INCOMPLETE,
                                             TestJob.CANCELED, TestJob.CANCELING),).values('status')
    url = reverse('lava.scheduler.failure_report')
    params = 'start=%s&end=%s&device_type=%s&health_check=%d' % (start_day, end_day, dt, health_check)
    return {
        'pass': res.filter(status=TestJob.COMPLETE).count(),
        'fail': res.exclude(status=TestJob.COMPLETE).count(),
        'date': start_date.strftime('%m-%d'),
        'failure_url': '%s?%s' % (url, params),
    }


def device_report_data(start_day, end_day, device, health_check):
    now = datetime.datetime.now()
    start_date = now + datetime.timedelta(start_day)
    end_date = now + datetime.timedelta(end_day)

    res = TestJob.objects.filter(actual_device=device, health_check=health_check,
                                 start_time__range=(start_date, end_date),
                                 status__in=(TestJob.COMPLETE, TestJob.INCOMPLETE,
                                             TestJob.CANCELED, TestJob.CANCELING),).values('status')
    url = reverse('lava.scheduler.failure_report')
    params = 'start=%s&end=%s&device=%s&health_check=%d' % (start_day, end_day, device, health_check)
    return {
        'pass': res.filter(status=TestJob.COMPLETE).count(),
        'fail': res.exclude(status=TestJob.COMPLETE).count(),
        'date': start_date.strftime('%m-%d'),
        'failure_url': '%s?%s' % (url, params),
    }


def job_report(start_day, end_day, health_check):
    now = datetime.datetime.now()
    start_date = now + datetime.timedelta(start_day)
    end_date = now + datetime.timedelta(end_day)

    res = TestJob.objects.filter(health_check=health_check,
                                 start_time__range=(start_date, end_date),
                                 status__in=(TestJob.COMPLETE, TestJob.INCOMPLETE,
                                             TestJob.CANCELED, TestJob.CANCELING),).values('status')
    url = reverse('lava.scheduler.failure_report')
    params = 'start=%s&end=%s&health_check=%d' % (start_day, end_day, health_check)
    return {
        'pass': res.filter(status=TestJob.COMPLETE).count(),
        'fail': res.exclude(status=TestJob.COMPLETE).count(),
        'date': start_date.strftime('%m-%d'),
        'failure_url': '%s?%s' % (url, params),
    }


@BreadCrumb("Reports", parent=lava_index)
def reports(request):
    health_day_report = []
    health_week_report = []
    job_day_report = []
    job_week_report = []
    for day in reversed(range(7)):
        health_day_report.append(job_report(day * -1 - 1, day * -1, True))
        job_day_report.append(job_report(day * -1 - 1, day * -1, False))
    for week in reversed(range(10)):
        health_week_report.append(job_report(week * -7 - 7, week * -7, True))
        job_week_report.append(job_report(week * -7 - 7, week * -7, False))

    long_running = TestJob.objects.filter(status__in=[TestJob.RUNNING,
                                                      TestJob.CANCELING]).order_by('start_time')[:5]

    return render_to_response(
        "lava_scheduler_app/reports.html",
        {
            'health_week_report': health_week_report,
            'health_day_report': health_day_report,
            'job_week_report': job_week_report,
            'job_day_report': job_day_report,
            'long_running': long_running,
            'bread_crumb_trail': BreadCrumbTrail.leading_to(index),
        },
        RequestContext(request))


class TagsColumn(Column):

    def render(self, value):
        return ', '.join([x.name for x in value.all()])


class FailedJobTable(JobTable):
    failure_tags = TagsColumn()
    failure_comment = Column()

    def get_queryset(self, request):
        failures = [TestJob.INCOMPLETE, TestJob.CANCELED, TestJob.CANCELING]
        jobs = all_jobs_with_custom_sort().filter(status__in=failures)

        health = request.GET.get('health_check', None)
        if health:
            jobs = jobs.filter(health_check=_str_to_bool(health))

        dt = request.GET.get('device_type', None)
        if dt:
            jobs = jobs.filter(actual_device__device_type__name=dt)

        device = request.GET.get('device', None)
        if device:
            jobs = jobs.filter(actual_device__hostname=device)

        start = request.GET.get('start', None)
        if start:
            now = datetime.datetime.now()
            start = now + datetime.timedelta(int(start))

            end = request.GET.get('end', None)
            if end:
                end = now + datetime.timedelta(int(end))
                jobs = jobs.filter(start_time__range=(start, end))
        return jobs

    class Meta:
        exclude = ('status', 'submitter', 'end_time', 'priority', 'description')

    datatable_opts = {
        'aaSorting': [[2, 'desc']],
    }


def failed_jobs_json(request):
    return FailedJobTable.json(request, params=(request,))


def _str_to_bool(string):
    return string.lower() in ['1', 'true', 'yes']


@BreadCrumb("Failure Report", parent=reports)
def failure_report(request):
    return render_to_response(
        "lava_scheduler_app/failure_report.html",
        {
            'device_type': request.GET.get('device_type', None),
            'device': request.GET.get('device', None),
            'failed_job_table': FailedJobTable(
                'failure_report',
                reverse(failed_jobs_json),
                params=(request,)
            ),
            'bread_crumb_trail': BreadCrumbTrail.leading_to(failure_report),
        },
        RequestContext(request))


@BreadCrumb("All Devices", parent=index)
def device_list(request):
    return render_to_response(
        "lava_scheduler_app/alldevices.html",
        {
            'devices_table': DeviceTable('devices', reverse(index_devices_json)),
            'bread_crumb_trail': BreadCrumbTrail.leading_to(device_list),
        },
        RequestContext(request))


@BreadCrumb("Active Devices", parent=index)
def active_device_list(request):
    return render_to_response(
        "lava_scheduler_app/activedevices.html",
        {
            'active_devices_table': ActiveDeviceTable('devices', reverse(index_devices_json)),
            'bread_crumb_trail': BreadCrumbTrail.leading_to(active_device_list),
        },
        RequestContext(request))


class MyDeviceTable(DeviceTable):

    def get_queryset(self, user):
        return Device.objects.owned_by_principal(user)

    datatable_opts = {
        'aaSorting': [[2, 'asc']],
        "iDisplayLength": 50
    }


def mydevices_json(request):
    return MyDeviceTable.json(request)


@BreadCrumb("My Devices", parent=index)
def mydevice_list(request):
    return render_to_response(
        "lava_scheduler_app/mydevices.html",
        {
            'my_device_table': MyDeviceTable('devices', reverse(mydevices_json),
                                             params=(request.user,)),
            'bread_crumb_trail': BreadCrumbTrail.leading_to(mydevice_list)

        },
        RequestContext(request))


def get_restricted_job(user, pk):
    """Returns JOB which is a TestJob object after checking for USER
    accessibility to the object.
    """
    job = TestJob.get_by_job_number(pk)

    if not job.is_accessible_by(user) and not user.is_superuser:
        raise PermissionDenied()
    return job


class SumIfSQL(models.sql.aggregates.Aggregate):
    is_ordinal = True
    sql_function = 'SUM'
    sql_template = 'SUM((%(condition)s)::int)'


class SumIf(models.Aggregate):
    name = 'SumIf'

    def add_to_query(self, query, alias, col, source, is_summary):
        aggregate = SumIfSQL(col,
                             source=source, is_summary=is_summary, **self.extra)
        query.aggregates[alias] = aggregate


class ActiveDeviceTable(DeviceTable):

    def get_queryset(self):
        return Device.objects.exclude(status=Device.RETIRED)

    datatable_opts = {
        'aaSorting': [[2, 'asc']],
        "iDisplayLength": 50
    }


class DeviceTypeTable(DataTablesTable):

    def get_queryset(self):
        return DeviceType.objects.filter(display=True)\
            .annotate(idle=SumIf('device', condition='status=%s' % Device.IDLE),
                      offline=SumIf('device', condition='status in (%s,%s)' %
                                                        (Device.OFFLINE, Device.OFFLINING)),
                      busy=SumIf('device', condition='status in (%s,%s)' %
                                                     (Device.RUNNING, Device.RESERVED)),
                      restricted=SumIf('device', condition='is_public is False'),
                      ).order_by('name')

    def render_display(self, record):
        return "%d idle, %d offline, %d busy, %d restricted" % (record.idle,
                                                                record.offline,
                                                                record.busy,
                                                                record.restricted)

    datatable_opts = {
        "iDisplayLength": 50
    }

    name = IDLinkColumn("name")
    # columns must match fields which actually exist in the relevant table.
    display = Column()

    searchable_columns = ['name']


class HealthJobSummaryTable(DataTablesTable):

    Duration = Column()
    Complete = Column()
    Failed = Column()


def device_type_json(request):
    return DeviceTypeTable.json(request)


class NoDTDeviceTable(DeviceTable):
    def get_queryset(self, device_type):
        return Device.objects.filter(device_type=device_type)

    class Meta:
        exclude = ('device_type',)


def populate_capabilities(dt):
    """
    device capabilities data retrieved from health checks
    param dt: a device type to check for capabilities.
    return: dict of capabilities based on the most recent health check.
    the returned dict contains a full set of empty values if no
    capabilities could be determined.
    """
    capability = {
        'capabilities_date': None,
        'processor': None,
        'models': None,
        'cores': 0,
        'emulated': False,
        'flags': [],
    }
    hardware_flags = []
    hardware_cpu_models = []
    use_health_job = dt.health_check_job != ""
    try:
        health_job = TestJob.objects.filter(
            actual_device__in=Device.objects.filter(device_type=dt),
            health_check=use_health_job,
            status=TestJob.COMPLETE).order_by('submit_time').reverse()[0]
    except IndexError:
        return capability
    if not health_job:
        return capability
    job = TestJob.objects.filter(id=health_job.id)[0]
    if not job:
        return capability
    bundle = job._results_bundle
    if not bundle:
        return capability
    bundle_json = bundle.get_sanitized_bundle().get_human_readable_json()
    if not bundle_json:
        return capability
    bundle_data = simplejson.loads(bundle_json)
    if 'hardware_context' not in bundle_data['test_runs'][0]:
        return capability
    # ok, we finally have a hardware_context for this device type, populate.
    devices = bundle_data['test_runs'][0]['hardware_context']['devices']
    capability['capabilities_date'] = job.end_time
    for device in devices:
        # multiple core cpus have multiple device.cpu entries, each with attributes.
        if device['device_type'] == 'device.cpu':
            if device['attributes']['cpu_type'] == '?':
                model = device['attributes']['model name']
            else:
                model = device['attributes']['cpu_type']
            if 'cpu_part' in device['attributes']:
                cpu_part = int(device['attributes']['cpu_part'], 16)
            elif 'CPU part' in device['attributes']:
                cpu_part = int(device['attributes']['CPU part'], 16)
            else:
                cpu_part = None
            if model.startswith("ARMv7") and cpu_part:
                if hex(cpu_part) == hex(0xc05):
                    model = "%s - %s" % (model, "Cortex A5")
                if hex(cpu_part) == hex(0xc07):
                    model = "%s - %s" % (model, "Cortex A7")
                if hex(cpu_part) == hex(0xc08):
                    model = "%s - %s" % (model, "Cortex A8")
                if hex(cpu_part) == hex(0xc09):
                    model = "%s - %s" % (model, "Cortex A9")
                if hex(cpu_part) == hex(0xc0f):
                    model = "%s - %s" % (model, "Cortex A15")
            hardware_cpu_models.append(model)
            if 'Features' in device['attributes']:
                hardware_flags.append(device['attributes']['Features'])
            elif 'flags' in device['attributes']:
                hardware_flags.append(device['attributes']['flags'])
            if device['attributes']['cpu_type'].startswith("QEMU"):
                capability['emulated'] = True
            capability['cores'] += 1
        if device['device_type'] == 'device.board':
            capability['processor'] = device['description']
    if len(hardware_flags) == 0:
        hardware_flags.append("None")
    if len(hardware_cpu_models) == 0:
        hardware_cpu_models.append("None")
    capability['models'] = ", ".join(hardware_cpu_models)
    capability['flags'] = ", ".join(hardware_flags)
    return capability


def index_nodt_devices_json(request, pk):
    device_type = get_object_or_404(DeviceType, pk=pk)
    return NoDTDeviceTable.json(request, params=(device_type,))


def device_type_jobs_json(request, pk):
    dt = get_object_or_404(DeviceType, pk=pk)
    return DeviceTypeJobTable.json(request, params=(dt,))


@BreadCrumb("Device Type {pk}", parent=index, needs=['pk'])
def device_type_detail(request, pk):
    dt = get_object_or_404(DeviceType, pk=pk)
    daily_complete = TestJob.objects.filter(
        actual_device__in=Device.objects.filter(device_type=dt),
        health_check=True,
        submit_time__gte=(datetime.datetime.now().date() - datetime.timedelta(days=1)),
        submit_time__lt=datetime.datetime.now().date(),
        status=TestJob.COMPLETE).count()
    daily_failed = TestJob.objects.filter(
        actual_device__in=Device.objects.filter(device_type=dt),
        health_check=True,
        submit_time__gte=(datetime.datetime.now().date() - datetime.timedelta(days=1)),
        submit_time__lt=datetime.datetime.now().date(),
        status=TestJob.INCOMPLETE).count()
    weekly_complete = TestJob.objects.filter(
        actual_device__in=Device.objects.filter(device_type=dt),
        health_check=True,
        submit_time__gte=(datetime.datetime.now().date() - datetime.timedelta(days=7)),
        submit_time__lt=datetime.datetime.now().date(),
        status=TestJob.COMPLETE).count()
    weekly_failed = TestJob.objects.filter(
        actual_device__in=Device.objects.filter(device_type=dt),
        health_check=True,
        submit_time__gte=(datetime.datetime.now().date() - datetime.timedelta(days=7)),
        submit_time__lt=datetime.datetime.now().date(),
        status=TestJob.INCOMPLETE).count()
    monthly_complete = TestJob.objects.filter(
        actual_device__in=Device.objects.filter(device_type=dt),
        health_check=True,
        submit_time__gte=(datetime.datetime.now().date() - datetime.timedelta(days=30)),
        submit_time__lt=datetime.datetime.now().date(),
        status=TestJob.COMPLETE).count()
    monthly_failed = TestJob.objects.filter(
        actual_device__in=Device.objects.filter(device_type=dt),
        health_check=True,
        submit_time__gte=(datetime.datetime.now().date() - datetime.timedelta(days=30)),
        submit_time__lt=datetime.datetime.now().date(),
        status=TestJob.INCOMPLETE).count()
    health_summary_data = [{
        "Duration": "24hours",
        "Complete": daily_complete,
        "Failed": daily_failed,
    }, {
        "Duration": "Week",
        "Complete": weekly_complete,
        "Failed": weekly_failed,
    }, {"Duration": "Month",
        "Complete": monthly_complete,
        "Failed": monthly_failed,
        }
    ]
    #  device capabilities data retrieved from health checks
    capabilities = populate_capabilities(dt)

    return render_to_response(
        "lava_scheduler_app/device_type.html",
        {
            'device_type': dt,
            'capabilities_date': capabilities['capabilities_date'],
            'processor': capabilities['processor'],
            'models': capabilities['models'],
            'cores': capabilities['cores'],
            'emulated': capabilities['emulated'],
            'flags': capabilities['flags'],
            'running_jobs_num': TestJob.objects.filter(
                actual_device__in=Device.objects.filter(device_type=dt),
                status=TestJob.RUNNING).count(),
            'queued_jobs_num': TestJob.objects.filter(
                Q(status=TestJob.SUBMITTED), Q(requested_device_type=dt)
                | Q(requested_device__in=Device.objects.filter(device_type=dt))).count(),
            'health_job_summary_table': HealthJobSummaryTable('device_type',
                                                              params=(dt,),
                                                              data=health_summary_data),
            'device_type_jobs_table': DeviceTypeJobTable(
                'device_type_jobs', reverse(device_type_jobs_json, kwargs=dict(pk=dt.pk)),
                params=(dt,)),
            'devices_table_no_dt': NoDTDeviceTable('devices', reverse(index_nodt_devices_json,
                                                                      kwargs=dict(pk=pk)), params=(dt,)),
            'bread_crumb_trail': BreadCrumbTrail.leading_to(device_type_detail, pk=pk),
            'context_help': BreadCrumbTrail.leading_to(device_type_detail, pk='help'),
        },
        RequestContext(request))


@BreadCrumb("{pk} device type report", parent=device_type_detail, needs=['pk'])
def device_type_reports(request, pk):
    device_type = get_object_or_404(DeviceType, pk=pk)
    health_day_report = []
    health_week_report = []
    job_day_report = []
    job_week_report = []
    for day in reversed(range(7)):
        health_day_report.append(type_report_data(day * -1 - 1, day * -1, device_type, True))
        job_day_report.append(type_report_data(day * -1 - 1, day * -1, device_type, False))
    for week in reversed(range(10)):
        health_week_report.append(type_report_data(week * -7 - 7, week * -7, device_type, True))
        job_week_report.append(type_report_data(week * -7 - 7, week * -7, device_type, False))

    long_running = TestJob.objects.filter(
        actual_device__in=Device.objects.filter(device_type=device_type),
        status__in=[TestJob.RUNNING,
                    TestJob.CANCELING]).order_by('start_time')[:5]

    return render_to_response(
        "lava_scheduler_app/devicetype_reports.html",
        {
            'device_type': device_type,
            'health_week_report': health_week_report,
            'health_day_report': health_day_report,
            'job_week_report': job_week_report,
            'job_day_report': job_day_report,
            'long_running': long_running,
            'bread_crumb_trail': BreadCrumbTrail.leading_to(device_type_reports, pk=pk),
        },
        RequestContext(request))


class DeviceHealthTable(DataTablesTable):

    def get_queryset(self):
        return Device.objects.select_related(
            "hostname", "last_health_report_job")

    def render_hostname(self, record):
        return mark_safe('<a href="%s">%s</a>' % (
            record.get_device_health_url(), escape(record.pk)))

    def render_last_health_report_job(self, record):
        report = record.last_health_report_job
        if report is None:
            return ''
        else:
            return pklink(report)

    hostname = Column("hostname")
    health_status = Column()
    last_report_time = DateColumn(
        verbose_name="last report time",
        accessor="last_health_report_job.end_time")
    last_health_report_job = Column("last report job")

    searchable_columns = ['hostname']
    datatable_opts = {
        "iDisplayLength": 25
    }


def lab_health_json(request):
    return DeviceHealthTable.json(request)


@BreadCrumb("All Device Health", parent=index)
def lab_health(request):
    return render_to_response(
        "lava_scheduler_app/labhealth.html",
        {
            'device_health_table': DeviceHealthTable(
                'device_health', reverse(lab_health_json)),
            'bread_crumb_trail': BreadCrumbTrail.leading_to(lab_health),
        },
        RequestContext(request))


class DeviceTypeJobTable(JobTable):

    def get_queryset(self, device_type):
        dt = get_object_or_404(DeviceType, pk=device_type)
        return all_jobs_with_custom_sort().filter(actual_device__in=Device.objects.filter(device_type=dt))

    datatable_opts = {
        'aaSorting': [[6, 'desc']],
        "iDisplayLength": 10,
    }


class HealthJobTable(JobTable):

    def get_queryset(self, device):
        return TestJob.objects.select_related("submitter",)\
            .filter(actual_device=device, health_check=True)

    class Meta:
        exclude = ('description', 'device')

    datatable_opts = {
        'aaSorting': [[4, 'desc']],
    }


def health_jobs_json(request, pk):
    device = get_object_or_404(Device, pk=pk)
    return HealthJobTable.json(params=(device,))


@BreadCrumb("All Health Jobs on Device {pk}", parent=index, needs=['pk'])
def health_job_list(request, pk):
    device = get_object_or_404(Device, pk=pk)

    return render_to_response(
        "lava_scheduler_app/health_jobs.html",
        {
            'device': device,
            'transition_table': DeviceTransitionTable(
                'transitions', reverse(transition_json, kwargs=dict(pk=device.pk)),
                params=(device,)),
            'health_job_table': HealthJobTable(
                'health_jobs', reverse(health_jobs_json, kwargs=dict(pk=pk)),
                params=(device,)),
            'show_forcehealthcheck': device.can_admin(request.user) and
            device.status not in [Device.RETIRED] and device.device_type.health_check_job != "",
            'can_admin': device.can_admin(request.user),
            'show_maintenance': device.can_admin(request.user) and
            device.status in [Device.IDLE, Device.RUNNING, Device.RESERVED],
            'edit_description': device.can_admin(request.user),
            'show_online': device.can_admin(request.user) and
            device.status in [Device.OFFLINE, Device.OFFLINING],
            'bread_crumb_trail': BreadCrumbTrail.leading_to(health_job_list, pk=pk),
        },
        RequestContext(request))


class AllJobsTable(JobTable):

    def get_queryset(self):
        return all_jobs_with_custom_sort()

    datatable_opts = JobTable.datatable_opts.copy()

    datatable_opts.update({
        'iDisplayLength': 25,
    })


class MyJobsTable(DataTablesTable):

    def render_device(self, record):
        if record.actual_device:
            return pklink(record.actual_device)
        elif record.requested_device:
            return pklink(record.requested_device)
        else:
            return mark_safe(
                '<i>' + escape(record.requested_device_type.pk) + '</i>')

    def render_description(self, value):
        if value:
            return value
        else:
            return ''

    sub_id = RestrictedIDLinkColumn(accessor="id")
    status = Column()
    priority = Column()
    device = Column(accessor='device_sort')
    description = Column(attrs=Attrs(width="30%"))
    submit_time = DateColumn()
    end_time = DateColumn()
    duration = Column(accessor='duration_sort')

    datatable_opts = {
        'aaSorting': [[5, 'desc']],
    }
    datatable_opts.update({
        'iDisplayLength': 25,
    })
    searchable_columns = ['description']

    def get_queryset(self, user):
        return my_jobs_with_custom_sort(user)


def myjobs_json(request):
    return MyJobsTable.json(request)


def alljobs_json(request):
    return AllJobsTable.json(request)


@BreadCrumb("All Jobs", parent=index)
def job_list(request):
    return render_to_response(
        "lava_scheduler_app/alljobs.html",
        {
            'bread_crumb_trail': BreadCrumbTrail.leading_to(job_list),
            'alljobs_table': AllJobsTable('alljobs', reverse(alljobs_json)),
        },
        RequestContext(request))


@BreadCrumb("Submit Job", parent=index)
def job_submit(request):

    is_authorized = False
    if request.user and request.user.has_perm(
            'lava_scheduler_app.add_testjob'):
        is_authorized = True

    response_data = {
        'is_authorized': is_authorized,
        'bread_crumb_trail': BreadCrumbTrail.leading_to(job_submit),
    }

    if request.method == "POST" and is_authorized:
        if request.is_ajax():
            try:
                validate_job_json(request.POST.get("json-input"))
                return HttpResponse(simplejson.dumps("success"))
            except Exception as e:
                return HttpResponse(simplejson.dumps(str(e)),
                                    mimetype="application/json")

        else:
            try:
                json_data = request.POST.get("json-input")
                job = TestJob.from_json_and_user(json_data, request.user)

                if isinstance(job, type(list())):
                    response_data["job_list"] = job
                else:
                    response_data["job_id"] = job.id
                return render_to_response(
                    "lava_scheduler_app/job_submit.html",
                    response_data, RequestContext(request))

            except (JSONDataError, ValueError, DevicesUnavailableException) \
                    as e:
                response_data["error"] = str(e)
                response_data["context_help"] = "lava scheduler submit job",
                response_data["json_input"] = request.POST.get("json-input")
                return render_to_response(
                    "lava_scheduler_app/job_submit.html",
                    response_data, RequestContext(request))

    else:
        return render_to_response(
            "lava_scheduler_app/job_submit.html",
            response_data, RequestContext(request))


@BreadCrumb("Job", parent=index, needs=['pk'])
def job_detail(request, pk):
    job = get_restricted_job(request.user, pk)

    data = {
        'job': job,
        'show_cancel': job.can_cancel(request.user),
        'show_failure': job.can_annotate(request.user),
        'show_resubmit': job.can_resubmit(request.user),
        'bread_crumb_trail': BreadCrumbTrail.leading_to(job_detail, pk=pk),
        'show_reload_page': job.status <= TestJob.RUNNING,
        'change_priority': job.can_change_priority(request.user),
        'context_help': BreadCrumbTrail.leading_to(job_detail, pk='detail'),
    }

    log_file = job.output_file()

    if log_file:
        job_errors = getDispatcherErrors(job.output_file())
        job_log_messages = getDispatcherLogMessages(job.output_file())

        levels = defaultdict(int)
        for kl in ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']:
            levels[kl] = 0
        for level, msg, _ in job_log_messages:
            levels[level] += 1
        levels = sorted(levels.items(), key=lambda (k, v): logging._levelNames.get(k))
        with job.output_file() as f:
            f.seek(0, 2)
            job_file_size = f.tell()
        data.update({
            'job_file_present': True,
            'job_errors': job_errors,
            'job_has_error': len(job_errors) > 0,
            'job_log_messages': job_log_messages,
            'levels': levels,
            'job_file_size': job_file_size,
        })
    else:
        data.update({
            'job_file_present': False,
        })

    return render_to_response(
        "lava_scheduler_app/job.html", data, RequestContext(request))


def job_definition(request, pk):
    job = get_restricted_job(request.user, pk)
    log_file = job.output_file()
    return render_to_response(
        "lava_scheduler_app/job_definition.html",
        {
            'job': job,
            'job_file_present': bool(log_file),
            'show_resubmit': job.can_resubmit(request.user),
        },
        RequestContext(request))


def job_definition_plain(request, pk):
    job = get_restricted_job(request.user, pk)
    response = HttpResponse(job.display_definition, mimetype='text/plain')
    response['Content-Disposition'] = "attachment; filename=job_%d.json" % \
        job.id
    return response


def multinode_job_definition(request, pk):
    job = get_restricted_job(request.user, pk)
    log_file = job.output_file()
    return render_to_response(
        "lava_scheduler_app/multinode_job_definition.html",
        {
            'job': job,
            'job_file_present': bool(log_file),
        },
        RequestContext(request))


def multinode_job_definition_plain(request, pk):
    job = get_restricted_job(request.user, pk)
    response = HttpResponse(job.multinode_definition, mimetype='text/plain')
    response['Content-Disposition'] = \
        "attachment; filename=multinode_job_%d.json" % job.id
    return response


@BreadCrumb("My Jobs", parent=index)
def myjobs(request):
    return render_to_response(
        "lava_scheduler_app/myjobs.html",
        {
            'bread_crumb_trail': BreadCrumbTrail.leading_to(myjobs),
            'myjobs_table': MyJobsTable('myjobs', reverse(myjobs_json),
                                        params=(request.user,)),
        },
        RequestContext(request))


@BreadCrumb("Complete log", parent=job_detail, needs=['pk'])
def job_log_file(request, pk):
    job = get_restricted_job(request.user, pk)
    content = formatLogFile(job.output_file())
    with job.output_file() as f:
        f.seek(0, 2)
        job_file_size = f.tell()
    return render_to_response(
        "lava_scheduler_app/job_log_file.html",
        {
            'job': TestJob.objects.get(pk=pk),
            'job_file_present': bool(job.output_file()),
            'sections': content,
            'job_file_size': job_file_size,
        },
        RequestContext(request))


def job_log_file_plain(request, pk):
    job = get_restricted_job(request.user, pk)
    response = HttpResponse(job.output_file(), mimetype='text/plain')
    response['Content-Disposition'] = "attachment; filename=job_%d.log" % job.id
    return response


def job_log_incremental(request, pk):
    start = int(request.GET.get('start', 0))
    job = get_restricted_job(request.user, pk)
    log_file = job.output_file()
    log_file.seek(start)
    new_content = log_file.read()
    m = getDispatcherLogMessages(StringIO.StringIO(new_content))
    response = HttpResponse(
        simplejson.dumps(m), content_type='application/json')
    response['X-Current-Size'] = str(start + len(new_content))
    if job.status not in [TestJob.RUNNING, TestJob.CANCELING]:
        response['X-Is-Finished'] = '1'
    return response


def job_full_log_incremental(request, pk):
    start = int(request.GET.get('start', 0))
    job = get_restricted_job(request.user, pk)
    log_file = job.output_file()
    log_file.seek(start)
    new_content = log_file.read()
    nl_index = new_content.rfind('\n', -NEWLINE_SCAN_SIZE)
    if nl_index >= 0:
        new_content = new_content[:nl_index + 1]
    m = formatLogFile(StringIO.StringIO(new_content))
    response = HttpResponse(
        simplejson.dumps(m), content_type='application/json')
    response['X-Current-Size'] = str(start + len(new_content))
    if job.status not in [TestJob.RUNNING, TestJob.CANCELING]:
        response['X-Is-Finished'] = '1'
    return response


LOG_CHUNK_SIZE = 512 * 1024
NEWLINE_SCAN_SIZE = 80


def job_output(request, pk):
    start = request.GET.get('start', 0)
    try:
        start = int(start)
    except ValueError:
        return HttpResponseBadRequest("invalid start")
    count_present = 'count' in request.GET
    job = get_restricted_job(request.user, pk)
    log_file = job.output_file()
    log_file.seek(0, os.SEEK_END)
    size = int(request.GET.get('count', log_file.tell()))
    if size - start > LOG_CHUNK_SIZE and not count_present:
        log_file.seek(-LOG_CHUNK_SIZE, os.SEEK_END)
        content = log_file.read(LOG_CHUNK_SIZE)
        nl_index = content.find('\n', 0, NEWLINE_SCAN_SIZE)
        if nl_index > 0 and not count_present:
            content = content[nl_index + 1:]
        skipped = size - start - len(content)
    else:
        skipped = 0
        log_file.seek(start, os.SEEK_SET)
        content = log_file.read(size - start)
    nl_index = content.rfind('\n', -NEWLINE_SCAN_SIZE)
    if nl_index >= 0 and not count_present:
        content = content[:nl_index + 1]
    response = HttpResponse(content)
    if skipped:
        response['X-Skipped-Bytes'] = str(skipped)
    response['X-Current-Size'] = str(start + len(content))
    if job.status not in [TestJob.RUNNING, TestJob.CANCELING]:
        response['X-Is-Finished'] = '1'
    return response


@post_only
def job_cancel(request, pk):
    job = get_restricted_job(request.user, pk)
    if job.can_cancel(request.user):
        if job.is_multinode:
            multinode_jobs = TestJob.objects.all().filter(
                target_group=job.target_group)
            for multinode_job in multinode_jobs:
                multinode_job.cancel()
        else:
            job.cancel()
        return redirect(job)
    else:
        return HttpResponseForbidden(
            "you cannot cancel this job", content_type="text/plain")


@post_only
def job_resubmit(request, pk):

    is_resubmit = request.POST.get("is_resubmit", False)

    response_data = {
        'is_authorized': False,
        'bread_crumb_trail': BreadCrumbTrail.leading_to(job_list),
    }

    job = get_restricted_job(request.user, pk)
    if job.can_resubmit(request.user):
        response_data["is_authorized"] = True

        if is_resubmit:
            try:
                job = TestJob.from_json_and_user(
                    request.POST.get("json-input"), request.user)

                if isinstance(job, type(list())):
                    response_data["job_list"] = job
                else:
                    response_data["job_id"] = job.id
                return render_to_response(
                    "lava_scheduler_app/job_submit.html",
                    response_data, RequestContext(request))

            except (JSONDataError, ValueError, DevicesUnavailableException) \
                    as e:
                response_data["error"] = str(e)
                response_data["json_input"] = request.POST.get("json-input")
                return render_to_response(
                    "lava_scheduler_app/job_submit.html",
                    response_data, RequestContext(request))
        else:
            if request.is_ajax():
                try:
                    validate_job_json(request.POST.get("json-input"))
                    return HttpResponse(simplejson.dumps("success"))
                except Exception as e:
                    return HttpResponse(simplejson.dumps(str(e)),
                                        mimetype="application/json")
            if job.is_multinode:
                definition = job.multinode_definition
            else:
                definition = job.display_definition

            try:
                response_data["json_input"] = definition
                return render_to_response(
                    "lava_scheduler_app/job_submit.html",
                    response_data, RequestContext(request))
            except (JSONDataError, ValueError, DevicesUnavailableException) \
                    as e:
                response_data["error"] = str(e)
                response_data["json_input"] = definition
                return render_to_response(
                    "lava_scheduler_app/job_submit.html",
                    response_data, RequestContext(request))

    else:
        return HttpResponseForbidden(
            "you cannot re-submit this job", content_type="text/plain")


class FailureForm(forms.ModelForm):
    class Meta:
        model = TestJob
        fields = ('failure_tags', 'failure_comment')


@post_only
def job_change_priority(request, pk):
    job = get_restricted_job(request.user, pk)
    if not job.can_change_priority(request.user):
        raise PermissionDenied()
    requested_priority = request.POST['priority']
    if job.priority != requested_priority:
        job.priority = requested_priority
        job.save()
    return redirect(job)


def job_annotate_failure(request, pk):
    job = get_restricted_job(request.user, pk)
    if not job.can_annotate(request.user):
        raise PermissionDenied()

    if request.method == 'POST':
        form = FailureForm(request.POST, instance=job)
        if form.is_valid():
            form.save()
            return redirect(job)
    else:
        form = FailureForm(instance=job)

    return render_to_response(
        "lava_scheduler_app/job_annotate_failure.html",
        {
            'form': form,
            'job': job,
        },
        RequestContext(request))


def job_json(request, pk):
    job = get_restricted_job(request.user, pk)
    json_text = simplejson.dumps({
        'status': job.get_status_display(),
        'results_link': request.build_absolute_uri(job.results_link),
    })
    content_type = 'application/json'
    if 'callback' in request.GET:
        json_text = '%s(%s)' % (request.GET['callback'], json_text)
        content_type = 'text/javascript'
    return HttpResponse(json_text, content_type=content_type)


@post_only
def get_remote_json(request):
    """Fetches remote json file."""
    url = request.POST.get("url")

    try:
        data = urllib2.urlopen(url).read()
        # Validate that the data at the location is really JSON.
        # This is security based check so noone can misuse this url.
        simplejson.loads(data)
    except Exception as e:
        return HttpResponse(simplejson.dumps(str(e)),
                            mimetype="application/json")

    return HttpResponse(data)


class RecentJobsTable(JobTable):

    def get_queryset(self, device):
        return device.recent_jobs()

    class Meta:
        exclude = ('device',)

    datatable_opts = {
        'aaSorting': [[5, 'desc']],
    }


def recent_jobs_json(request, pk):
    device = get_object_or_404(Device, pk=pk)
    return RecentJobsTable.json(request, params=(device,))


class DeviceTransitionTable(DataTablesTable):

    def get_queryset(self, device):
        qs = device.transitions.select_related('created_by')
        return qs

    def render_created_on(self, record):
        t = record
        base = "<a href='/scheduler/transition/%s'>%s</a>" \
               % (record.id, filters.date(t.created_on, "Y-m-d H:i"))
        return mark_safe(base)

    def render_transition(self, record):
        t = record
        return mark_safe(
            '%s &rarr; %s' % (t.get_old_state_display(), t.get_new_state_display(),))

    created_on = Column('when', attrs=Attrs(width="40%"))
    transition = Column('transition', sortable=False, accessor='old_state')
    created_by = Column('by')
    message = TemplateColumn('''
    <div class="edit_transition" id="{{ record.id }}" style="width: 100%">{{ record.message }}</div>
        ''')

    datatable_opts = {
        'aaSorting': [[0, 'desc']],
    }


def transition_json(request, pk):
    device = get_object_or_404(Device, pk=pk)
    return DeviceTransitionTable.json(request, params=(device,))


@post_only
def edit_transition(request):
    """Edit device state transition, based on user permission."""
    id = request.POST.get("id")
    value = request.POST.get("value")

    transition_obj = get_object_or_404(DeviceStateTransition, pk=id)
    if transition_obj.device.can_admin(request.user):
        transition_obj.update_message(value)
        return HttpResponse(transition_obj.message)
    else:
        return HttpResponseForbidden("Permission denied.",
                                     content_type="text/plain")


@BreadCrumb("Transition {pk}", parent=index, needs=['pk'])
def transition_detail(request, pk):
    transition = get_object_or_404(DeviceStateTransition, id=pk)
    return render_to_response(
        "lava_scheduler_app/transition.html",
        {
            'device': transition.device,
            'transition': transition,
            'transition_table': DeviceTransitionTable(
                'transitions', reverse(transition_json, kwargs=dict(pk=transition.device.pk)),
                params=(transition.device,)),
            'bread_crumb_trail': BreadCrumbTrail.leading_to(transition_detail, pk=pk),
            'old_state': transition.get_old_state_display(),
            'new_state': transition.get_new_state_display(),
        },
        RequestContext(request))


@BreadCrumb("Device {pk}", parent=index, needs=['pk'])
def device_detail(request, pk):
    device = get_object_or_404(Device, pk=pk)
    if device.status in [Device.OFFLINE, Device.OFFLINING]:
        try:
            transition = device.transitions.filter(message__isnull=False).latest('created_on').message
        except DeviceStateTransition.DoesNotExist:
            transition = None
    else:
        transition = None
    return render_to_response(
        "lava_scheduler_app/device.html",
        {
            'device': device,
            'transition': transition,
            'transition_table': DeviceTransitionTable(
                'transitions', reverse(transition_json, kwargs=dict(pk=device.pk)),
                params=(device,)),
            'recent_job_table': RecentJobsTable(
                'jobs', reverse(recent_jobs_json, kwargs=dict(pk=device.pk)),
                params=(device,)),
            'show_forcehealthcheck': device.can_admin(request.user) and
            device.status not in [Device.RETIRED] and device.device_type.health_check_job != "",
            'can_admin': device.can_admin(request.user),
            'show_maintenance': device.can_admin(request.user) and
            device.status in [Device.IDLE, Device.RUNNING, Device.RESERVED],
            'edit_description': device.can_admin(request.user),
            'show_online': (device.can_admin(request.user) and
                            device.status in [Device.OFFLINE, Device.OFFLINING]),
            'show_restrict': (device.is_public and device.can_admin(request.user)
                              and device.status not in [Device.RETIRED]),
            'show_pool': (not device.is_public and device.can_admin(request.user)
                          and device.status not in [Device.RETIRED]),
            'bread_crumb_trail': BreadCrumbTrail.leading_to(device_detail, pk=pk),
            'context_help': BreadCrumbTrail.show_help(device_detail, pk="help"),
        },
        RequestContext(request))


@BreadCrumb("{pk} device report", parent=device_detail, needs=['pk'])
def device_reports(request, pk):
    device = get_object_or_404(Device, pk=pk)
    health_day_report = []
    health_week_report = []
    job_day_report = []
    job_week_report = []
    for day in reversed(range(7)):
        health_day_report.append(device_report_data(day * -1 - 1, day * -1, device, True))
        job_day_report.append(device_report_data(day * -1 - 1, day * -1, device, False))
    for week in reversed(range(10)):
        health_week_report.append(device_report_data(week * -7 - 7, week * -7, device, True))
        job_week_report.append(device_report_data(week * -7 - 7, week * -7, device, False))

    long_running = TestJob.objects.filter(
        actual_device=device,
        status__in=[TestJob.RUNNING,
                    TestJob.CANCELING]).order_by('start_time')[:5]

    return render_to_response(
        "lava_scheduler_app/device_reports.html",
        {
            'device': device,
            'health_week_report': health_week_report,
            'health_day_report': health_day_report,
            'job_week_report': job_week_report,
            'job_day_report': job_day_report,
            'long_running': long_running,
            'bread_crumb_trail': BreadCrumbTrail.leading_to(device_reports, pk=pk),
        },
        RequestContext(request))


@post_only
def device_maintenance_mode(request, pk):
    device = Device.objects.get(pk=pk)
    if device.can_admin(request.user):
        device.put_into_maintenance_mode(request.user, request.POST.get('reason'),
                                         request.POST.get('notify'))
        return redirect(device)
    else:
        return HttpResponseForbidden(
            "you cannot administer this device", content_type="text/plain")


@post_only
def device_online(request, pk):
    device = Device.objects.get(pk=pk)
    if device.can_admin(request.user):
        device.put_into_online_mode(request.user, request.POST.get('reason'),
                                    request.POST.get('skiphealthcheck'))
        return redirect(device)
    else:
        return HttpResponseForbidden(
            "you cannot administer this device", content_type="text/plain")


@post_only
def device_looping_mode(request, pk):
    device = Device.objects.get(pk=pk)
    if device.can_admin(request.user):
        device.put_into_looping_mode(request.user, request.POST.get('reason'))
        return redirect(device)
    else:
        return HttpResponseForbidden(
            "you cannot administer this device", content_type="text/plain")


@post_only
def device_force_health_check(request, pk):
    device = Device.objects.get(pk=pk)
    if device.can_admin(request.user):
        job = device.initiate_health_check_job()
        return redirect(job)
    else:
        return HttpResponseForbidden(
            "you cannot administer this device", content_type="text/plain")


def device_edit_description(request, pk):
    device = Device.objects.get(pk=pk)
    if device.can_admin(request.user):
        device.description = request.POST.get('desc')
        device.save()
        return redirect(device)
    else:
        return HttpResponseForbidden(
            "you cannot edit the description of this device", content_type="text/plain")


@post_only
def device_restrict_device(request, pk):
    device = Device.objects.get(pk=pk)
    if device.can_admin(request.user):
        message = "Restriction added: %s" % request.POST.get('reason')
        device.is_public = False
        DeviceStateTransition.objects.create(
            created_by=request.user, device=device, old_state=device.status,
            new_state=device.status, message=message, job=None).save()
        device.save()
        return redirect(device)
    else:
        return HttpResponseForbidden(
            "you cannot restrict submissions to this device", content_type="text/plain")


@post_only
def device_derestrict_device(request, pk):
    device = Device.objects.get(pk=pk)
    if device.can_admin(request.user):
        message = "Restriction removed: %s" % request.POST.get('reason')
        device.is_public = True
        DeviceStateTransition.objects.create(
            created_by=request.user, device=device, old_state=device.status,
            new_state=device.status, message=message, job=None).save()
        device.save()
        return redirect(device)
    else:
        return HttpResponseForbidden(
            "you cannot derestrict submissions to this device", content_type="text/plain")


@BreadCrumb("Worker", parent=index, needs=['pk'])
def worker_detail(request, pk):
    worker = get_object_or_404(Worker, pk=pk)
    return render_to_response(
        "lava_scheduler_app/worker.html",
        {
            'worker': worker,
            'worker_device_table': WorkerDeviceTable(
                'worker', reverse(worker_device_json,
                                  kwargs=dict(pk=worker.pk)),
                params=(worker,)),
            'can_admin': worker.can_admin(request.user),
            'bread_crumb_trail': BreadCrumbTrail.leading_to(worker_detail,
                                                            pk=pk),
        },
        RequestContext(request))


@post_only
def edit_worker_desc(request):
    """Edit worker description, based on user permission."""

    pk = request.POST.get("id")
    value = request.POST.get("value")
    worker_obj = get_object_or_404(Worker, pk=pk)

    if worker_obj.can_admin(request.user):
        worker_obj.update_description(value)
        return HttpResponse(worker_obj.get_description())
    else:
        return HttpResponseForbidden("Permission denied.",
                                     content_type="text/plain")
