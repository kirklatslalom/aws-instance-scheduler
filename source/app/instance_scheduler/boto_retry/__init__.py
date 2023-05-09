# -*- coding: utf-8 -*-
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0


import os

import boto3
import botocore.config


def get_client_with_standard_retry(service_name, region=None, session=None):
    """
    Creates a bot3 client for the specified service name and region. The return client will have additional method for the
    specified methods that are wrapped with the logic of the specified wait strategy or the default strategy for that service.
    The method names must be valid for the boto3 service client. The name of the added functions is the name of the original
    function plus the (default) value of method_suffix parameter
    :param service_name: Name of the service
    :param region: Region for the client
    :param session: Boto3 session, if None a new session will be created
    :return: Client for the service with additional method that use retry logic
    """
    args = {
        "service_name": service_name,
        "region_name": region,
        "config": standard_retries_client_config(),
    }

    aws_session = session if session is not None else boto3.Session()

    result = aws_session.client(**args)

    return result


def standard_retries_client_config():
    return botocore.config.Config(
        user_agent_extra=os.getenv("USER_AGENT_EXTRA", None),
        retries={"max_attempts": 5, "mode": "standard"},
    )
