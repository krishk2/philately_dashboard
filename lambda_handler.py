"""AWS Lambda entry point (Function URL / API Gateway HTTP API payload v2).

Two kinds of events:
  * HTTP requests from the Function URL -> served by the Flask app through apig-wsgi.
  * {"task": "run-jobs"} from EventBridge Scheduler -> one pass of the background job queue
    (expire unpaid pre-bookings, refresh feeds, run queued NPDA jobs).
"""

from apig_wsgi import make_lambda_handler

from app import create_app

app = create_app()
_http = make_lambda_handler(app, binary_support=True)


def handler(event, context):
    if isinstance(event, dict) and event.get("task") == "run-jobs":
        from app.services.jobs import run_once

        with app.app_context():
            return run_once()
    return _http(event, context)
