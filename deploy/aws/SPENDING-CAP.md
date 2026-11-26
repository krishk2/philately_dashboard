# Capping AWS spend at your $50 of credits

**AWS has no hard stop.** Credits just run out, and anything after that goes on your card. So use
three layers. Do all of them.

## 1. Bound the worst case (needs a quota increase on new accounts)

> New AWS accounts have a Lambda concurrency quota of 10 and must keep all 10 unreserved, so
> `put-function-concurrency` is rejected until you request a higher quota (Service Quotas →
> Lambda → Concurrent executions). This account is in that state, so layer 2 is the main guard.

Limit how much compute the app can ever use, whatever the traffic:

```bash
aws lambda put-function-concurrency --function-name philately-web --reserved-concurrent-executions 2
```

Lambda bills GB-seconds. At 2 concurrent executions with 512 MB running *non-stop* for 30 days:
2 × 0.5 GB × 2,592,000 s = 2.59M GB-s × $0.0000166667 ≈ **$43 per month absolute maximum**
(before the free 400,000 GB-s). Real traffic is a tiny fraction. Extra visitors get an error
page instead of a bill. Keep the function memory at 512 MB or less.

## 2. Kill switch at a budget threshold

Budgets update only every 8-24 hours, so this is a backstop, not an instant stop.

1. **SNS topic** `philately-budget-alarm` (the budget publishes here).
2. **Lambda** `philately-killswitch`: the code in `killswitch.py`; subscribe it to the topic; give
   its role `lambda:PutFunctionConcurrency` on `philately-web`.
3. **Budget** `philately-50-credits`: cost budget, **$50, monthly (or "fixed" for the credit
   period)**. Add alerts at 20% ($10), 50% ($25), 80% ($40) by email, and at **90% ($45) to
   the SNS topic** so the kill switch fires before you hit $50.
4. The budget is created with **credits excluded** (`IncludeCredit=false`), so it tracks gross usage.
   Otherwise credits would offset the spend and it would show $0 and never fire. Check the first
   few days against Billing → Bills.
5. Test it: `aws lambda invoke --function-name philately-killswitch /dev/null`, confirm the app
   returns errors, then re-enable it with `aws lambda delete-function-concurrency ...`.

## 3. Don't create anything that bills while idle

Avoid: NAT Gateway, load balancers, RDS/Aurora, ElastiCache, Secrets Manager, EC2 left running,
Elastic IPs, CloudFront with large traffic. The setup in this folder uses none (Lambda +
Function URL + S3 + SSM Parameter Store + EventBridge Scheduler, with the database on Neon).

## Checks

- Turn on **Billing → Free Tier usage alerts** and **Cost Anomaly Detection**.
- Billing → **Credits** shows the remaining credit balance and expiry date. Credits also expire.
- Every week, glance at Billing → Bills.
- Delete everything when you're done: `aws lambda delete-function ...`, empty and delete the
  S3 bucket, delete ECR images, delete the budget.

## What is actually deployed (this account)

* Function `philately-web` in **eu-central-1** (next to the Neon database), 512 MB, Function URL.
* Budget `philately-50-credits` ($50/month, credits excluded). Alerts at 20/50/80% and a forecast
  go to SNS topic `philately-budget-info` (add an email subscription to get them). At **90%** the
  topic `philately-budget-alarm` triggers `philately-killswitch` (us-east-1), which deletes the
  Function URL and sets concurrency to 0. The kill switch was tested on a dummy function.
* Re-enable after a trip: see the header of `killswitch.py`.
