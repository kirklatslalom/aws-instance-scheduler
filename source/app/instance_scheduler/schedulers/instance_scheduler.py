# -*- coding: utf-8 -*-
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0


from __future__ import print_function

import os
from datetime import datetime

import boto3
import json
import pytz
from botocore.exceptions import ClientError
from instance_scheduler import configuration
from instance_scheduler import schedulers
from instance_scheduler.boto_retry import (
    get_client_with_standard_retry,
    standard_retries_client_config,
)
from instance_scheduler.configuration.instance_schedule import InstanceSchedule
from .instance_states import InstanceStates
from instance_scheduler.util.metrics import send_metrics_data, allow_send_metrics
from instance_scheduler.util.named_tuple_builder import as_namedtuple
from instance_scheduler.util.scheduler_metrics import SchedulerMetrics

ERR_ASSUMING_ROLE = "Can not assume role {} for account {}, ({}))"
ERR_INVALID_ARN = "{} is not a valid ARN"
ERR_INVALID_REGION = "{} is not a valid region name"
ERR_SETTING_INSTANCE_TYPE = "Error changing instance type ({})"

INF_DESIRED_TYPE = ", desired type is {}"
INF_PROCESSING_ACCOUNT = "Running {} scheduler for account {}{} in region(s) {}"
INF_STARTING_INSTANCES = "Starting instances {} in region {}"
INF_STOPPED_INSTANCES = "Stopping instances {} in region {}"
INF_MAINTENANCE_WINDOW = (
    'Maintenance window "{}" used as running period found for instance {}'
)

INF_DO_NOT_STOP_RETAINED_INSTANCE = (
    "Instance {} was already running at start of period and schedule uses retain option, desired "
    "state set to {} but instance will not be stopped if it is still running."
)

WARN_DUPLICATE_ACCOUNT = "Account {} is already processed, skipping "
WARN_SKIPPING_UNKNOWN_SCHEDULE = (
    'Skipping instance {} in region {} for account {}, schedule name "{}" is unknown'
)
WARN_RESIZE_NOT_SUPPORTED = "Instance {} with type {} does not support resizing"

DEBUG_STOPPED_REGION_INSTANCES = (
    "Listing instance {} in region {} to be stopped by scheduler"
)
DEBUG_INSTANCE_HEADER = "[ Instance {} ]"
DEBUG_NEW_INSTANCE = (
    'New instance "{}" will not be stopped until next scheduling period'
)
DEBUG_CURRENT_INSTANCE_STATE = (
    'Current state is {}, instance type is {}, schedule is "{}"'
)
DEBUG_SKIPPING_TERMINATED_INSTANCE = (
    'Skipping terminated instance "{}" in region {} for account {}'
)
DEBUG_STARTED_REGION_INSTANCES = (
    "Listing instance {} in region {} with instance type {} to be started by scheduler"
)
DEBUG_CURRENT_AND_DESIRED_STATE = (
    'Desired state for instance from schedule "{}" is {}, last desired state was {}, '
    "actual state is {}{} "
)
DEBUG_ENFORCED_STATE = "Using enforcement flag of schedule to set actual state of instance {} from {} to {}"
DEBUG_APPLY_RETAIN_RUNNING_STATE = (
    "Desired state is {} for instance {} which is already running and retain running period is "
    "used the schedule, desired state set to {} so it is not stopped automatically at the end of "
    "this running period"
)


class InstanceScheduler:
    """
    Implements scheduler logic
    """

    def __init__(self, service, scheduler_configuration):
        """
        Initializes instance of instance scheduler
        :param service: service strategy that handles the actual listing, starting and stopping of the instances of that service
        """
        self._service = service
        self._instance_states = None
        self._schedule_metrics = None
        self._sts_client = None
        self._scheduled_instances = []
        self._configuration = None
        self._scheduler_start_list = []
        self._scheduler_stop_list = []
        self._schedule_resize_list = []
        self._scheduler_configuration = scheduler_configuration
        self._stack_name = os.getenv(configuration.ENV_STACK, "")
        self._lambda_account = os.getenv(configuration.ENV_ACCOUNT)
        self._logger = None
        self._context = None
        self._lambda_client = None

        self._usage_metrics = {"Started": {}, "Stopped": {}, "Resized": {}}

    @property
    def lambda_client(self):
        """
        Get the lambda client
        :return: lambda client
        """
        if self._lambda_client is None:
            self._lambda_client = get_client_with_standard_retry("lambda")
        return self._lambda_client

    @property
    def _regions(self):
        if len(self._configuration.regions) > 0:
            return self._configuration.regions
        else:
            # no regions, use region of lambda function
            return [boto3.Session().region_name]

    @property
    def _sts(self):
        if self._sts_client is None:
            session = boto3.Session()
            sts_regional_endpoint = str.format(
                "https://sts.{}.amazonaws.com", session.region_name
            )
            self._sts_client = session.client(
                "sts",
                region_name=session.region_name,
                endpoint_url=sts_regional_endpoint,
                config=standard_retries_client_config(),
            )

        return self._sts_client

    def remove_account_from_config(self, aws_account, cross_account_role):
        """
        This method will invoke the lambda to remove the aws_account from the configuration, it calls the lambda handler eventbus_request_handler,
        and sends payload which will update the config by removing the account from further scheduling.
        {
            "account": 111122223333,
            "detail-type": "Parameter Store Change",
            "detail": {
                "operation": "Delete"
            }
        }
        :param aws_account: account where the assume role permission is not available for the lambda role to assume.
        :param cross_account_role: role name for logging message to SNS.
        """
        try:
            self._logger.error(
                "Removing the account {} from scheduling configuration as assume role permission is missing for the iam role {}".format(
                    aws_account, cross_account_role
                )
            )
            payload = str.encode(
                json.dumps(
                    {
                        "account": aws_account,
                        "detail-type": "Parameter Store Change",
                        "detail": {"operation": "Delete"},
                    }
                )
            )
            response = self.lambda_client.invoke(
                FunctionName=self._context.function_name,
                InvocationType="Event",
                LogType="None",
                Payload=payload,
            )
            self._logger.info(
                "Removing account {} from configuration".format(aws_account)
            )
            self._logger.debug(
                "Lambda response {} for removing account from configuration".format(
                    response
                )
            )
        except Exception as ex:
            self._logger.error(
                "Error invoking lambda {} error {}".format(
                    self._context.function_name, ex
                )
            )

    @property
    def _accounts(self):
        def get_session_for_account(cross_account_role, aws_account):
            # get a token for the cross account role and use it to create a session
            try:
                session_name = "{}-scheduler-{}".format(
                    self._service.service_name, aws_account
                )
                # assume a role
                token = self._sts.assume_role(
                    RoleArn=cross_account_role, RoleSessionName=session_name
                )
                credentials = token["Credentials"]
                # create a session using the assumed role credentials
                return boto3.Session(
                    aws_access_key_id=credentials["AccessKeyId"],
                    aws_secret_access_key=credentials["SecretAccessKey"],
                    aws_session_token=credentials["SessionToken"],
                )
            except ClientError as ex:
                self._logger.error(
                    "Error Code {}".format(ex.response.get("Error", {}).get("Code"))
                )
                if ex.response.get("Error", {}).get("Code") == "AccessDenied":
                    self.remove_account_from_config(
                        aws_account=aws_account, cross_account_role=cross_account_role
                    )
                else:
                    self._logger.error(
                        ERR_ASSUMING_ROLE.format(
                            cross_account_role, aws_account, str(ex)
                        )
                    )
                    return None

        # keep track of accounts processed
        accounts_done = []

        # return session for lambda account if processing instances in that account
        if self._configuration.schedule_lambda_account:
            accounts_done.append(self._lambda_account)
            yield as_namedtuple(
                "Account",
                {
                    "session": boto3.Session(),
                    "name": self._lambda_account,
                    "role": None,
                },
            )

        # iterate through remote accounts
        for account in self._configuration.remote_account_ids:
            if account in accounts_done:
                self._logger.warning(WARN_DUPLICATE_ACCOUNT, account)
                continue

            # get a session for the role
            role = f"arn:{self._configuration.aws_partition}:iam::{account}:role/{self._configuration.namespace}-{self._configuration.scheduler_role_name}"
            session = get_session_for_account(role, account)
            if session is not None:
                yield as_namedtuple(
                    "Account", {"session": session, "name": account, "role": role}
                )

    def _instance_display_str(self, inst_id, name):
        s = "{}:{}".format(self._service.service_name.upper(), inst_id)
        if name:
            s += " ({})".format(name)
        return s

    def _scheduled_instances_in_region(self, account, region):
        # use service strategy to get a list of instances that can be scheduled for that service
        for instance in self._service.get_schedulable_instances(
            {
                schedulers.PARAM_SESSION: account.session,
                schedulers.PARAM_ACCOUNT: account.name,
                schedulers.PARAM_ROLE: account.role,
                schedulers.PARAM_REGION: region,
                schedulers.PARAM_CONFIG: self._configuration,
                schedulers.PARAM_LOGGER: self._logger,
                schedulers.PARAM_CONTEXT: self._context,
            }
        ):
            instance["account"] = account.name
            instance["region"] = region
            instance["service"] = self._service.service_name
            instance["instance_str"] = self._instance_display_str(
                instance["id"], instance["name"]
            )
            inst = as_namedtuple(
                self._service.service_name + "Instance", instance, excludes=["tags"]
            )
            yield inst

    def run(
        self, state_table, scheduler_config, logger, lambda_account=None, context=None
    ):
        """
        Runs the scheduler for a service
        :param state_table: name of the instance state table
        :param scheduler_config: scheduler configuration data
        :param logger: logger to log output of scheduling process
        :param lambda_account: lambda account
        :param context: Lambda context
        :return:
        """

        self._lambda_account = lambda_account
        self._configuration = scheduler_config
        self._logger = logger
        self._context = context
        self._logger.debug_enabled = self._configuration.trace

        # stored instance desired states
        self._instance_states = InstanceStates(
            state_table, self._service.service_name, self._logger, self._context
        )

        # time to use for metrics
        self._schedule_metrics = SchedulerMetrics(datetime.utcnow(), self._context)

        # response to caller, contains list off all processed accounts with started and stopped instances
        response = {}

        for account in self._accounts:
            response[account.name] = self._process_account(account)

        if allow_send_metrics():
            self._send_usage_metrics()

        return response

    def get_desired_state_and_type(self, schedule, instance):
        # test if the instance has a maintenance window in which it must be running
        if (
            instance.maintenance_window is not None
            and schedule.use_maintenance_window is True
        ):
            self._logger.info(
                INF_MAINTENANCE_WINDOW, instance.maintenance_window.name, instance.id
            )

            # get the desired start for the maintenance window at current UTC time
            (
                inst_state,
                inst_type,
                running_period,
            ) = instance.maintenance_window.get_desired_state(
                instance,
                logger=self._logger,
                dt=datetime.utcnow().replace(tzinfo=pytz.timezone("UTC")),
            )

            # if we're in the maintenance window return running state
            if inst_state == InstanceSchedule.STATE_RUNNING:
                return inst_state, inst_type

        # based on the schedule get the desired state and instance type for this instance
        inst_state, inst_type, _ = schedule.get_desired_state(
            instance, logger=self._logger
        )
        return inst_state, inst_type

    def _process_account(self, account):
        # processes instances for a service in an account
        started_instances = {}
        stopped_instances = {}
        resized_instances = {}

        self._logger.info(
            INF_PROCESSING_ACCOUNT,
            self._service.service_name.upper(),
            account.name,
            " using role " + account.role if account.role else "",
            ", ".join(self._configuration.regions),
        )

        for region in self._regions:
            state_loaded = False
            instances = []

            self._scheduler_start_list = []
            self._scheduler_stop_list = []
            self._schedule_resize_list = []

            for instance in self._scheduled_instances_in_region(account, region):
                # delay loading instance state until first instance is returned
                if not state_loaded:
                    self._instance_states.load(account.name, region)
                    state_loaded = True

                instances.append(instance)

                # handle terminated instances
                if instance.is_terminated:
                    self._logger.debug(
                        DEBUG_SKIPPING_TERMINATED_INSTANCE,
                        instance.instance_str,
                        region,
                        instance.account,
                    )
                    self._instance_states.delete_instance_state(instance.id)
                    continue

                # get the schedule for this instance
                instance_schedule = self._configuration.get_schedule(
                    instance.schedule_name
                )
                if not instance_schedule:
                    self._logger.warning(
                        WARN_SKIPPING_UNKNOWN_SCHEDULE,
                        instance.instance_str,
                        region,
                        instance.account,
                        instance.schedule_name,
                    )
                    continue

                self._logger.debug(DEBUG_INSTANCE_HEADER, instance.instance_str)
                self._logger.debug(
                    DEBUG_CURRENT_INSTANCE_STATE,
                    instance.current_state,
                    instance.instancetype,
                    instance_schedule.name,
                )

                # based on the schedule get the desired state and instance type for this instance
                desired_state, desired_type = self.get_desired_state_and_type(
                    instance_schedule, instance
                )

                # get the  previous desired instance state
                last_desired_state = self._instance_states.get_instance_state(
                    instance.id
                )
                self._logger.debug(
                    DEBUG_CURRENT_AND_DESIRED_STATE,
                    instance_schedule.name,
                    desired_state,
                    last_desired_state,
                    instance.current_state,
                    INF_DESIRED_TYPE.format(desired_type) if desired_type else "",
                )

                # last desired state None means this is the first time the instance is seen by the scheduler
                if last_desired_state is InstanceSchedule.STATE_UNKNOWN:
                    # new instances that are running are optionally not stopped to allow them to finish possible initialization
                    if (
                        instance.is_running
                        and desired_state == InstanceSchedule.STATE_STOPPED
                    ):
                        if not instance_schedule.stop_new_instances:
                            self._instance_states.set_instance_state(
                                instance.id, InstanceSchedule.STATE_STOPPED
                            )
                            self._logger.debug(
                                DEBUG_NEW_INSTANCE, instance.instance_str
                            )
                            continue
                        self._process_new_desired_state(
                            account,
                            region,
                            instance,
                            desired_state,
                            desired_type,
                            last_desired_state,
                            instance_schedule.retain_running,
                        )
                    else:
                        self._process_new_desired_state(
                            account,
                            region,
                            instance,
                            desired_state,
                            desired_type,
                            last_desired_state,
                            instance_schedule.retain_running,
                        )

                # existing instance

                # if enforced check the actual state with the desired state enforcing the schedule state
                elif instance_schedule.enforced:
                    if (
                        instance.is_running
                        and desired_state == InstanceSchedule.STATE_STOPPED
                    ) or (
                        not instance.is_running
                        and desired_state == InstanceSchedule.STATE_RUNNING
                    ):
                        self._logger.debug(
                            DEBUG_ENFORCED_STATE,
                            instance.instance_str,
                            InstanceSchedule.STATE_RUNNING
                            if instance.is_running
                            else InstanceSchedule.STATE_STOPPED,
                            desired_state,
                        )
                        self._process_new_desired_state(
                            account,
                            region,
                            instance,
                            desired_state,
                            desired_type,
                            last_desired_state,
                            instance_schedule.retain_running,
                        )
                # if not enforced then compare the schedule state with the actual state so state of manually started/stopped
                # instance it will honor that state
                elif last_desired_state != desired_state:
                    self._process_new_desired_state(
                        account,
                        region,
                        instance,
                        desired_state,
                        desired_type,
                        last_desired_state,
                        instance_schedule.retain_running,
                    )

                self._schedule_metrics.add_schedule_metrics(
                    self._service.service_name, instance_schedule, instance
                )

            # process lists of instances that must be started or stopped
            self._start_and_stop_instances(account, region=region)

            # cleanup desired instance states and save
            self._instance_states.cleanup([i.id for i in instances])
            self._instance_states.save()

            # build output structure, hold started, stopped and resized instances per region
            if len(self._scheduler_start_list) > 0:
                started_instances[region] = [
                    {i.id: {"schedule": i.schedule_name}}
                    for i in self._scheduler_start_list
                ]
            if len(self._scheduler_stop_list):
                stopped_instances[region] = [
                    {i.id: {"schedule": i.schedule_name}}
                    for i in self._scheduler_stop_list
                ]
            if len(self._schedule_resize_list) > 0:
                resized_instances[region] = [
                    {
                        i[0].id: {
                            "schedule": i[0].schedule_name,
                            "old": i[0].instancetype,
                            "new": i[1],
                        }
                    }
                    for i in self._schedule_resize_list
                ]
            if allow_send_metrics():
                self._collect_usage_metrics()

        # put cloudwatch metrics
        if self._configuration.use_metrics:
            self._schedule_metrics.put_schedule_metrics()

        # output data
        result = {"started": started_instances, "stopped": stopped_instances}
        if self._service.allow_resize:
            result["resized"] = resized_instances
        return result

    def _send_usage_metrics(self):
        usage_data = []
        for s in list(self._usage_metrics):
            if len(self._usage_metrics[s]) == 0:
                del self._usage_metrics[s]
        if len(self._usage_metrics) > 0:
            for action in self._usage_metrics:
                for instance_type in self._usage_metrics.get(action, {}):
                    usage_data.append(
                        {
                            "Service": self._service.service_name,
                            "Action": action,
                            "InstanceType": instance_type,
                            "Instances": self._usage_metrics[action][instance_type],
                        }
                    )

            send_metrics_data(usage_data, logger=self._logger)

    def _collect_usage_metrics(self):
        for i in self._scheduler_start_list:
            if i.id in [r[0].id for r in self._schedule_resize_list]:
                instance_type = [
                    r[1] for r in self._schedule_resize_list if r[0].id == i.id
                ][0]
            else:
                instance_type = i.instancetype
            if instance_type in self._usage_metrics["Started"]:
                self._usage_metrics["Started"][instance_type] += 1
            else:
                self._usage_metrics["Started"][instance_type] = 1

        for i in self._scheduler_stop_list:
            if i.instancetype in self._usage_metrics["Stopped"]:
                self._usage_metrics["Stopped"][i.instancetype] += 1
            else:
                self._usage_metrics["Stopped"][i.instancetype] = 1

        for i in self._schedule_resize_list:
            type_change = "{}-{}".format(i[0].instancetype, i[1])
            if type_change in self._usage_metrics["Resized"]:
                self._usage_metrics["Resized"][type_change] += 1
            else:
                self._usage_metrics["Resized"][type_change] = 1

    # handle new state of an instance
    def _process_new_desired_state(
        self,
        account,
        region,
        instance,
        desired_state,
        desired_type,
        last_desired_state,
        retain_running,
    ):
        def need_and_can_resize():
            if desired_type is not None and instance.instancetype != desired_type:
                if not instance.allow_resize:
                    self._logger.warning(
                        WARN_RESIZE_NOT_SUPPORTED,
                        instance.instance_str,
                        instance.instancetype,
                    )
                    return False
                else:
                    return True
            return False

        def resize_instance(inst, new_type):
            try:
                # adjust instance type before starting using the resize_instance method in the service_strategy
                self._service.resize_instance(
                    {
                        schedulers.PARAM_SESSION: account.session,
                        schedulers.PARAM_ACCOUNT: account.name,
                        schedulers.PARAM_ROLE: account.role,
                        schedulers.PARAM_REGION: region,
                        schedulers.PARAM_TRACE: self._configuration.trace,
                        schedulers.PARAM_INSTANCE: instance,
                        schedulers.PARAM_DESIRED_TYPE: new_type,
                        schedulers.PARAM_LOGGER: self._logger,
                        schedulers.PARAM_CONTEXT: self._context,
                        schedulers.PARAM_CONFIG: self._scheduler_configuration,
                    }
                )

                self._schedule_resize_list.append((inst, new_type))
            except Exception as ex:
                # if changing the instance type does fail do not add instance to start list so it is handled a next time
                self._logger.error(ERR_SETTING_INSTANCE_TYPE, str(ex))

        # last desired status was saved as retain-running
        if last_desired_state == InstanceSchedule.STATE_RETAIN_RUNNING:
            # don't change last desired state desired whilst in a running period
            if desired_state == InstanceSchedule.STATE_RUNNING:
                pass

            # save last desired state as stopped (but do not stop) at the end of running period
            elif desired_state == InstanceSchedule.STATE_STOPPED:
                # safe new desired stopped state but keep running
                self._logger.debug(
                    INF_DO_NOT_STOP_RETAINED_INSTANCE,
                    instance.id,
                    InstanceSchedule.STATE_STOPPED,
                )
                self._instance_states.set_instance_state(
                    instance.id, InstanceSchedule.STATE_STOPPED
                )
            else:
                # just save new desired state
                self._instance_states.set_instance_state(instance.id, desired_state)
        else:
            if desired_state == InstanceSchedule.STATE_RUNNING:
                if not instance.is_running:
                    inst_type = (
                        desired_type
                        if desired_type is not None
                        else instance.instancetype
                    )
                    self._logger.debug(
                        DEBUG_STARTED_REGION_INSTANCES,
                        instance.instance_str,
                        instance.region,
                        inst_type,
                    )

                    # for instances to be started test if resizing is required
                    if need_and_can_resize():
                        resize_instance(instance, desired_type)

                    # append instance to list of instances to start
                    self._scheduler_start_list.append(instance)

                # instance already running with desired state of running
                else:
                    # if retain running option is used in this save desired state as retained running.
                    if last_desired_state == InstanceSchedule.STATE_STOPPED:
                        if retain_running:
                            self._logger.debug(
                                DEBUG_APPLY_RETAIN_RUNNING_STATE,
                                desired_state,
                                instance.id,
                                InstanceSchedule.STATE_RETAIN_RUNNING,
                            )
                            self._instance_states.set_instance_state(
                                instance.id, InstanceSchedule.STATE_RETAIN_RUNNING
                            )
                        else:
                            # instance is running, set last desired state from stopped to started
                            self._instance_states.set_instance_state(
                                instance.id, InstanceSchedule.STATE_RUNNING
                            )

                            # desired state is running but saved state already saves as retain running

            elif desired_state in [
                InstanceSchedule.STATE_STOPPED,
                InstanceSchedule.STATE_STOPPED_FOR_RESIZE,
            ]:
                if instance.is_running:
                    # instance needs to be stopped
                    self._logger.debug(
                        DEBUG_STOPPED_REGION_INSTANCES,
                        instance.instance_str,
                        instance.region,
                    )
                    # append instance to list of instances to start
                    if desired_state == InstanceSchedule.STATE_STOPPED_FOR_RESIZE:
                        instance = instance._replace(resized=True)
                    self._scheduler_stop_list.append(instance)
                    # stopped instance with desired state of running but in retained state mode
                    # (manually stopped in running period and already running at start)
                else:
                    # just save new desired state
                    self._instance_states.set_instance_state(
                        instance.id, InstanceSchedule.STATE_STOPPED
                    )

            else:
                self._instance_states.set_instance_state(instance.id, desired_state)

    # start and stop listed instances
    def _start_and_stop_instances(self, account, region):
        if len(self._scheduler_start_list) > 0:
            self._logger.info(
                INF_STARTING_INSTANCES,
                ", ".join([i.instance_str for i in self._scheduler_start_list]),
                region,
            )

            for inst_id, state in self._service.start_instances(
                {
                    schedulers.PARAM_SESSION: account.session,
                    schedulers.PARAM_ACCOUNT: account.name,
                    schedulers.PARAM_ROLE: account.role,
                    schedulers.PARAM_REGION: region,
                    schedulers.PARAM_TRACE: self._configuration.trace,
                    schedulers.PARAM_STARTED_INSTANCES: self._scheduler_start_list,
                    schedulers.PARAM_LOGGER: self._logger,
                    schedulers.PARAM_CONTEXT: self._context,
                    schedulers.PARAM_STACK: self._stack_name,
                    schedulers.PARAM_CONFIG: self._scheduler_configuration,
                }
            ):
                # set state based on returned state from start action
                self._instance_states.set_instance_state(inst_id, state)

        if len(self._scheduler_stop_list) > 0:
            self._logger.info(
                INF_STOPPED_INSTANCES,
                ", ".join([i.instance_str for i in self._scheduler_stop_list]),
                region,
            )
            for inst_id, state in self._service.stop_instances(
                {
                    schedulers.PARAM_SESSION: account.session,
                    schedulers.PARAM_ACCOUNT: account.name,
                    schedulers.PARAM_ROLE: account.role,
                    schedulers.PARAM_REGION: region,
                    schedulers.PARAM_TRACE: self._configuration.trace,
                    schedulers.PARAM_STOPPED_INSTANCES: self._scheduler_stop_list,
                    schedulers.PARAM_LOGGER: self._logger,
                    schedulers.PARAM_CONTEXT: self._context,
                    schedulers.PARAM_STACK: self._stack_name,
                    schedulers.PARAM_CONFIG: self._scheduler_configuration,
                }
            ):
                # set state based on start of stop action
                self._instance_states.set_instance_state(inst_id, state)
