#!/usr/bin/env python3
import os

import aws_cdk as cdk

from ddb_os_stack import DynamoDBOpenSearchStack2


app = cdk.App()
DynamoDBOpenSearchStack2(app, "DynamoDBOpenSearchStack2",
    )

app.synth()
