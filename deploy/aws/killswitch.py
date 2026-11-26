"""Spending kill switch: a tiny Lambda that turns the app off when the budget alarm fires.

AWS Budgets publishes to an SNS topic -> this function (1) deletes the app's public Function URL,
so nobody can reach it, and (2) sets reserved concurrency to 0 as a second layer. No compute is
billed while the app is off. Data in Neon and S3 is untouched.

Turn the app back on:
  aws lambda create-function-url-config --function-name philately-web --auth-type NONE
  aws lambda delete-function-concurrency --function-name philately-web
  (add --region eu-central-1 to both)
  (the URL changes; update the Google redirect URI)

Environment: APP_FUNCTION (default philately-web), APP_REGION (default eu-central-1).
"""

import json
import os

import boto3

NAME = os.environ.get("APP_FUNCTION", "philately-web")
REGION = os.environ.get("APP_REGION", "eu-central-1")


def handler(event, context):
    lam = boto3.client("lambda", region_name=REGION)
    done = []
    for label, call in (
        ("function_url_deleted", lambda: lam.delete_function_url_config(FunctionName=NAME)),
        (
            "concurrency_zero",
            lambda: lam.put_function_concurrency(FunctionName=NAME, ReservedConcurrentExecutions=0),
        ),
    ):
        try:
            call()
            done.append(label)
        except Exception as exc:  # noqa: BLE001 - keep going so the other layer still applies
            print(f"{label} failed: {exc}")
    print(json.dumps({"function": NAME, "done": done, "reason": "budget threshold reached"}))
    return {"done": done}
