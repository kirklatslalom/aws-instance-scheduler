# -*- coding: utf-8 -*-
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from contextlib import contextmanager
from os import environ
from instance_scheduler.requesthandlers.eventbus_request_handler import (
    EventBusRequestHandler,
)
from instance_scheduler.util.dynamodb_utils import DynamoDBUtils

table_name = environ["CONFIG_TABLE"]


@contextmanager
def ddb_setup(dynamodb_client_resource):
    dynamodb_client_resource.create_table(
        TableName=table_name,
        KeySchema=[
            {"AttributeName": "name", "KeyType": "HASH"},
            {"AttributeName": "type", "KeyType": "HASH"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "name", "AttributeType": "S"},
            {"AttributeName": "type", "AttributeType": "S"},
        ],
        ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
    )
    yield


def test_handler(dynamodb_client_resource):
    with ddb_setup(dynamodb_client_resource):
        createEvent = {
            "version": "0",
            "id": "c4556ddf-88ad-1b22-e482-19f2d72eb7e3",
            "detail-type": "Parameter Store Change",
            "source": "aws.ssm",
            "account": "111111111111",
            "time": "2021-08-19T06:26:38Z",
            "region": "us-east-1",
            "resources": [
                "arn:aws:ssm:us-east-1:111111111111:parameter/scheduler/do-not-delete-manually/static"
            ],
            "detail": {
                "name": "/scheduler/do-not-delete-manually/static",
                "type": "String",
                "operation": "Create",
            },
        }
        dynamodb_table = DynamoDBUtils.get_dynamodb_table_resource_ref(table_name)
        dynamodb_table.put_item(
            Item={"type": "config", "name": "scheduler", "namespace": "static"}
        )
        handler = EventBusRequestHandler(createEvent, {})
        response = handler.is_handling_request(createEvent)
        assert response == True

        response = handler.handle_request()
        assert response == "Exiting event bus request handler"
        deleteEvent = {
            "version": "0",
            "id": "c4556ddf-88ad-1b22-e482-19f2d72eb7e3",
            "detail-type": "Parameter Store Change",
            "source": "aws.ssm",
            "account": "111111111111",
            "time": "2021-08-19T06:26:38Z",
            "region": "us-east-1",
            "resources": [
                "arn:aws:ssm:us-east-1:111111111111:parameter/scheduler/do-not-delete-manually/static"
            ],
            "detail": {
                "name": "/scheduler/do-not-delete-manually/static",
                "type": "String",
                "operation": "Delete",
            },
        }
        handler = EventBusRequestHandler(deleteEvent, {})
        response = handler.is_handling_request(deleteEvent)
        assert response == True

        response = handler.handle_request()
        assert response == "Exiting event bus request handler"
