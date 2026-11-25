#!/bin/sh
# Container entrypoint. The same image runs three roles:
#   web      (default)  gunicorn serving the site
#   worker              background jobs and periodic tasks (flask worker)
#   migrate             apply database migrations and exit (run once per deploy)
#   run-jobs            one pass of the job queue, for scheduled tasks
set -e

case "${1:-web}" in
  web)
    if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then flask db upgrade; fi
    exec gunicorn -c gunicorn.conf.py wsgi:app
    ;;
  worker)   exec flask worker ;;
  migrate)  exec flask db upgrade ;;
  run-jobs) exec flask run-jobs ;;
  *)        exec "$@" ;;
esac
