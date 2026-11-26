# Deploying to AWS

## Scale-to-zero (what is deployed): Lambda + Neon + S3, no Docker needed

```bash
uv run python scripts/build_lambda.py        # builds dist/philately-lambda.zip (Linux wheels)
# upload the zip to a bucket in the function's region, then:
aws lambda update-function-code --function-name philately-web --region eu-central-1 \n    --s3-bucket <deploy-bucket> --s3-key philately-lambda.zip
# schema changes: run `flask db upgrade` from your machine with DATABASE_URL set to Neon
```

* `lambda_handler.py` serves HTTP (Function URL) and `{"task": "run-jobs"}` events from an
  EventBridge schedule (every 6 h: expire holds, refresh feeds).
* Region: keep the function in the same region as the database. Neon here is in Frankfurt, so the
  function is in eu-central-1; S3 uploads can live in another region.
* Cost/safety: see [SPENDING-CAP.md](SPENDING-CAP.md) and [iam-deployer-policy.json](iam-deployer-policy.json).
* Add `https://<function-url>/login/google/callback` to the Google OAuth client's redirect URIs.

---

## Always-on alternative: ECS Fargate
One Docker image runs every role (`web`, `worker`, `migrate`, `run-jobs`; see
`docker-entrypoint.sh`). The recommended setup is **ECS on Fargate**, which runs containers
without managing servers.

```
                 Route 53 (optional domain)
                          │
          ACM certificate │ HTTPS
                          ▼
              Application Load Balancer ── health check: GET /healthz
                          │
        ┌─────────────────┴──────────────────┐
        ▼                                    ▼
  ECS service "web"                   ECS service "worker"
  Fargate, 2 tasks                    Fargate, 1 task
  gunicorn (gthread)                  flask worker: NPDA standing orders,
                                      hold expiry, feed imports
        │            │                       │
        ▼            ▼                       ▼
  RDS PostgreSQL   S3 bucket (uploads)   (same DB)
  private subnet   + optional CloudFront

  Secrets Manager: SECRET_KEY, DATABASE_URL, GEMINI_API_KEY, OAuth secrets
  CloudWatch Logs: container stdout (gunicorn access + app logs)
  ECR: the image          ElastiCache Redis (optional): shared rate-limit counters
```

## Rough monthly cost (ap-south-1, check current pricing)

| Piece | Size | Notes |
|---|---|---|
| ALB | 1 | the largest fixed cost of a small setup |
| Fargate web | 2 × 0.5 vCPU / 1 GB | scale on CPU; password hashing is the CPU hot spot |
| Fargate worker | 1 × 0.25 vCPU / 0.5 GB | |
| RDS PostgreSQL | db.t4g.micro, 20 GB gp3 | may be covered by the free tier depending on your account |
| S3 | a few GB | cents |
| ElastiCache | cache.t4g.micro | optional. Without it each task keeps its own rate-limit counters. |

**Avoid the NAT Gateway trap.** A NAT Gateway costs about as much as everything else combined.
Either run the tasks in public subnets with "assign public IP" and a security group that only
allows traffic from the ALB, or add VPC endpoints for S3, ECR, Secrets Manager and CloudWatch.

**Cheaper alternatives:** App Runner (web) + RDS + S3, with `JOBS_INLINE=1` (jobs run in the web
process) and an EventBridge Scheduler rule that runs the image's `run-jobs` command as an ECS task
every 10 minutes. Or a single Lightsail instance running `docker compose` for a demo.

## One-time setup (console or AWS CLI)

1. **ECR**: create repository `philately`.
2. **RDS**: PostgreSQL 16, `db.t4g.micro`, private, a security group that allows 5432 from the ECS
   tasks' security group. Note the endpoint.
3. **S3**: bucket for uploads (block public access on). Either keep it private (the app serves
   short-lived signed URLs) or put CloudFront in front and set `S3_PUBLIC_URL`.
4. **Secrets Manager**: one secret per value: `SECRET_KEY` (`python -c "import secrets;
   print(secrets.token_hex(32))"`), `DATABASE_URL`
   (`postgresql+psycopg://user:pass@endpoint:5432/philately`), optional `GEMINI_API_KEY`,
   `GOOGLE_CLIENT_SECRET`, `YAHOO_CLIENT_SECRET`.
5. **IAM**:
   * task execution role: `AmazonECSTaskExecutionRolePolicy` + `secretsmanager:GetSecretValue`
     on those secrets;
   * task role: `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` on `arn:aws:s3:::BUCKET/uploads/*`.
     Use the task role instead of access keys: leave `S3_ACCESS_KEY_ID` unset and boto3 picks up the
     role automatically.
6. **ECS cluster** (Fargate). Register `task-definition.json` (replace the `<…>` placeholders)
   as `philately-web`, and a copy as `philately-worker` with `"command": ["worker"]`, no
   `portMappings`, no `healthCheck` and `"cpu": "256", "memory": "512"`. Then:
   * run the `migrate` task once;
   * create service **web** behind an ALB target group on port 8000, health check `/healthz`;
   * create service **worker** with 1 task, no load balancer.
7. **ALB**: HTTPS listener with an ACM certificate, HTTP→HTTPS redirect.
8. **OAuth**: add `https://YOUR_DOMAIN/login/google/callback` and `…/login/yahoo/callback` as
   redirect URIs in the Google and Yahoo consoles. (`TRUST_PROXY` is on in production, so the app
   builds `https://` URLs behind the ALB.)

## Deploying new versions

`.github/workflows/deploy-aws.yml` (manual trigger) builds the image, pushes it to ECR, runs
migrations, and rolls both services. It signs in with GitHub OIDC, so no AWS keys are stored in
GitHub. Set the repository variables it lists before the first run.

## Load testing a deployment

Point Locust at a **staging** copy, never production (the scenarios create orders):

```bash
uv run --with locust locust -f loadtest/locustfile.py --host https://staging.YOUR_DOMAIN
```

Run with `RATELIMIT_ENABLED=0` on staging, or the login limits will block the simulated users.
Watch CloudWatch for CPU on the web tasks and RDS connections, and add web tasks (or ECS target
tracking on CPU) until the 95th percentile meets your goal.
