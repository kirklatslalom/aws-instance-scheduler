# -*- coding: utf-8 -*-
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0


import copy
import os
from datetime import datetime

from instance_scheduler import configuration

# class to hold the configuration for the instance scheduler
INF_SCHEDULE_DISPLAY = (
    "Configuration:\n"
    'Scheduled services = "{}"\n'
    'Schedule clusters = "{}"\n'
    'Create RDS instance snapshot = "{}"\n'
    'Tagname = "{}"\n'
    'Default timezone = "{}"\n'
    'Trace = "{}"\n'
    'Enable SSM Maintenance Windows = "{}"\n'
    'Use metrics = "{}"\n'
    'Regions = "{}"\n'
    'Started tags = "{}"\n'
    'Stopped tags = "{}"\n'
    'Process Lambda account = "{}"\n'
    'Scheduler Role Name = "{}"\n'
    'Namespace = "{}"\n'
    'Organization Id = "{}"\n'
    'Aws Partition = "{}"\n'
    'Remote Account Ids = "{}"'
)

TAG_VAL_STR = "{{{}}}"


class SchedulerConfig:
    """
    Implements scheduler configuration
    """

    def __init__(
        self,
        scheduled_services,
        schedule_clusters,
        tag_name,
        regions,
        default_timezone,
        schedules,
        trace,
        enable_SSM_maintenance_windows,
        use_metrics,
        remote_account_ids,
        namespace,
        aws_partition,
        scheduler_role_name,
        organization_id,
        schedule_lambda_account,
        create_rds_snapshot,
        started_tags=None,
        stopped_tags=None,
    ):
        """
        Initializes schedule configuration instance
        :param scheduled_services: services handled by the scheduler
        :param schedule_clusters schedule RDS multi-AZ and Aurora clusters
        :param tag_name: name of the tag to define schedule for instances
        :param regions: regions to handle
        :param default_timezone: default timezone for schedules
        :param schedules: instance running schedules
        :param trace: set to true for detailed logging
        :param enable_SSM_maintenance_windows: set to true for enable solution to retrieve SSM Maintenance Windows.
        :param use_metrics: global flag to enable metrics collection
        :param remote_account_ids: remote account ids
        :param namespace: namespace for the stack
        :param aws_partition: the aws partition where the solution is installed.
        :param scheduler_role_name: scheduler role name
        :param organization_id: organization id
        :param schedule_lambda_account: set to true to schedule instances in account in which scheduler is installed
        :param create_rds_snapshot create snapshot before stopping non-cluster rds instances
        :param started_tags: start tags in string format
        :param stopped_tags: stop tags in string format
        """
        self.tag_name = tag_name
        self.schedules = schedules
        self.default_timezone = default_timezone
        self.trace = trace
        self.enable_SSM_maintenance_windows = enable_SSM_maintenance_windows
        self.use_metrics = use_metrics
        self.regions = regions
        self.remote_account_ids = remote_account_ids
        self.namespace = namespace
        self.aws_partition = aws_partition
        self.organization_id = organization_id
        self.scheduler_role_name = scheduler_role_name
        self.schedule_lambda_account = schedule_lambda_account
        self.scheduled_services = scheduled_services
        self.schedule_clusters = schedule_clusters
        self.create_rds_snapshot = create_rds_snapshot
        self._service_settings = None
        self.started_tags = (
            []
            if started_tags in ["" or None]
            else self.tag_list(self.build_tags_from_template(started_tags))
        )
        self.stopped_tags = (
            []
            if stopped_tags in ["" or None]
            else self.tag_list(self.build_tags_from_template(stopped_tags))
        )

    def get_schedule(self, name):
        """
        Get a schedule by its name
        :param name: name of the schedule
        :return: Schedule, None f it does not exist
        """
        return self.schedules[name] if name in self.schedules else None

    @classmethod
    def build_tags_from_template(cls, tags_str, tag_variables=None):
        lastkey = None
        tags = {}
        for tag in tags_str.split(","):
            if "=" in tag:
                t = tag.partition("=")
                tags[t[0]] = t[2]
                lastkey = t[0]
            elif lastkey is not None:
                tags[lastkey] = ",".join([tags[lastkey], tag])

        tag_vars = {} if tag_variables is None else copy.copy(tag_variables)

        dt = datetime.utcnow()
        tag_vars.update(
            {
                configuration.TAG_VAL_SCHEDULER: os.getenv(configuration.ENV_STACK, ""),
                configuration.TAG_VAL_YEAR: "{:0>4d}".format(dt.year),
                configuration.TAG_VAL_MONTH: "{:0>2d}".format(dt.month),
                configuration.TAG_VAL_DAY: "{:0>2d}".format(dt.day),
                configuration.TAG_VAL_HOUR: "{:0>2d}".format(dt.hour),
                configuration.TAG_VAL_MINUTE: "{:0>2d}".format(dt.minute),
                configuration.TAG_VAL_TIMEZONE: "UTC",
            }
        )

        for tag in tags:
            value = tags[tag]
            if value not in ["", None]:
                for v in tag_vars:
                    tags[tag] = tags[tag].replace(TAG_VAL_STR.format(v), tag_vars[v])
        return tags

    @classmethod
    def tag_list(cls, tags_dict):
        valid_tags = {
            tag_key: tags_dict[tag_key]
            for tag_key in tags_dict
            if not (tag_key.startswith("aws:") or tag_key.startswith("cloudformation:"))
        }
        return (
            [{"Key": t, "Value": tags_dict[t]} for t in tags_dict]
            if valid_tags is not None
            else []
        )

    def __str__(self):
        s = INF_SCHEDULE_DISPLAY.format(
            ", ".join(self.scheduled_services),
            str(self.schedule_clusters),
            str(self.create_rds_snapshot),
            self.tag_name,
            self.default_timezone,
            str(self.trace),
            str(self.enable_SSM_maintenance_windows),
            str(self.use_metrics),
            ", ".join(self.regions),
            str(self.started_tags),
            str(self.stopped_tags),
            str(self.schedule_lambda_account),
            str(self.scheduler_role_name),
            str(self.namespace),
            str(self.organization_id),
            str(self.aws_partition),
            ", ".join(self.remote_account_ids),
        )

        return s
