"""Spending kill switch: a tiny Lambda that turns the app off when the budget alarm fires.

AWS Budgets publishes to an SNS topic -> this function sets the app's *reserved concurrency to 0*,
which makes Lambda reject every invocation immediately (no compute is billed). Turn the app back
on with:   aws lambda delete-function-concurrency --function-name philately-web

Environment: APP_FUNCTIONS = comma-separated function names (default "philately-web").
Needs lambda:PutFunctionConcurrency on those functions.
"""

import json
import os

import boto3

lam = boto3.client("lambda")


def handler(event, context):
    names = [n.strip() for n in os.environ.get("APP_FUNCTIONS", "philately-web").split(",") if n.strip()]
    for name in names:
        lam.put_function_concurrency(FunctionName=name, ReservedConcurrentExecutions=0)
    print(json.dumps({"disabled": names, "reason": "budget threshold reached"}))
    return {"disabled": names}
