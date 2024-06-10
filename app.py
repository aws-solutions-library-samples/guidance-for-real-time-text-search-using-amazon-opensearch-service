#!/usr/bin/env python3
import os

import aws_cdk as cdk

from ddb_os_stack import DynamoDBOpenSearchStack


app = cdk.App()
DynamoDBOpenSearchStack(app, "DynamoDBOpenSearchStack",
    )

app.synth()
