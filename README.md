# POC: ECS Scale to Zero based on SQS — with S3 File Processing

## Architecture

See full diagram → [architecture.md](./architecture.md)

```
S3 (file drop)
    │ S3 Event (ObjectCreated)
    ▼
Lambda Chunker
    │ sends chunk descriptors {s3_key, byte_start, byte_end}
    ▼
SQS Queue ──► CloudWatch Alarm ──► App Auto Scaling
                                          │
                  ECS Fargate Cluster     │
             ┌────────────────────────┐   │
             │  Worker Tasks (0 → N) │◄──┘
             │  poll → S3 Range read │
             │  parse rows → delete  │
             └────────────────────────┘
```

- A `.csv` file dropped in S3 triggers Lambda, which splits it into line-aligned chunks and sends descriptors to SQS
- Workers fetch their chunk via S3 Range reads, parse each row as a business event
- When queue empties, CloudWatch alarm fires → ECS scales back to 0

---

## Prerequisites

- AWS CLI configured (`aws sts get-caller-identity` to verify)
- Terraform >= 1.5
- Docker Desktop
- Python 3.x — on Windows use `py` launcher (`py --version` to verify)

---

## Deployed Resources (Account: 022784798356, Region: us-east-1)

| Resource | Name / ARN |
|---|---|
| S3 Input Bucket | `poc-ecs-sqs-input-022784798356` |
| Lambda Chunker | `poc-ecs-sqs-chunker` |
| ECR Repository | `poc-ecs-sqs-worker` |
| SQS Queue | `poc-ecs-sqs-queue` |
| SQS Queue URL | `https://sqs.us-east-1.amazonaws.com/022784798356/poc-ecs-sqs-queue` |
| SQS DLQ | `poc-ecs-sqs-dlq` |
| ECS Cluster | `poc-ecs-sqs-cluster` |
| ECS Service | `poc-ecs-sqs-service` |
| CloudWatch Log Group (worker) | `/ecs/poc-ecs-sqs` (retention: 7 days) |
| CloudWatch Log Group (lambda) | `/aws/lambda/poc-ecs-sqs-chunker` (retention: 7 days) |
| CloudWatch Alarm (scale-out) | `poc-ecs-sqs-scale-out` |
| CloudWatch Alarm (scale-in) | `poc-ecs-sqs-scale-in` |
| IAM Task Role | `poc-ecs-sqs-task-role` |
| IAM Execution Role | `poc-ecs-sqs-exec-role` |
| IAM Lambda Role | `poc-ecs-sqs-lambda-chunker-role` |
| VPC | `poc-ecs-sqs-vpc` (10.0.0.0/16) |

---

## Step 1 — Build & Push Docker Image

```bash
AWS_ACCOUNT=022784798356
AWS_REGION=us-east-1

# Create ECR repo (skip if already exists)
aws ecr create-repository --repository-name poc-ecs-sqs-worker --region $AWS_REGION

# Login to ECR
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com

# Build, tag, push
cd app
docker build -t poc-ecs-sqs-worker .
docker tag poc-ecs-sqs-worker:latest $AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/poc-ecs-sqs-worker:latest
docker push $AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/poc-ecs-sqs-worker:latest
```

---

## Step 2 — Deploy Terraform

```bash
cd terraform
terraform init
terraform apply -var="container_image=$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/poc-ecs-sqs-worker:latest"
```

Terraform creates all resources including: VPC, S3 bucket, Lambda chunker, SQS queue + DLQ, ECS cluster/service/task definition, IAM roles, CloudWatch alarms, App Auto Scaling.

Note the `s3_input_bucket` from the output.

---

## Step 3 — Drop a CSV File (triggers the pipeline)

Upload any `.csv` file with a header row to the S3 input bucket:

```bash
# Windows
aws s3 cp your-file.csv s3://poc-ecs-sqs-input-022784798356/

# Linux/macOS
aws s3 cp your-file.csv s3://$(terraform output -raw s3_input_bucket)/
```

Expected CSV format (header required):

```
event_id,event_type,payload
1,ORDER_PLACED,{"item":"abc"}
2,ORDER_PLACED,{"item":"xyz"}
...
```

Lambda will automatically chunk the file into groups of 1000 lines and send each chunk as an SQS message.

---

## Step 4 — Observe Scale to Zero

Once all chunks are processed, the CloudWatch alarm fires after ~2 minutes (2 consecutive 0-message periods) and ECS scales back to 0 tasks.

---

## Testing Guide

### Test 1 — Generate a test CSV

Use the included generator script to produce a CSV with any number of rows:

```bash
# Windows — generates test_events.csv with 3000 rows (3 chunks of 1000)
py gen_test.py
```

Or generate inline for a custom row count:

```bash
# Windows
py -c "
lines = ['event_id,event_type,payload']
for i in range(1, 5001):
    lines.append(f'{i},ORDER_PLACED,{{\"item\":\"product_{i}\"}}') 
open('test_events.csv', 'w').write('\n'.join(lines) + '\n')
print('done')
"
```

Expected CSV format (header row required):

```
event_id,event_type,payload
1,ORDER_PLACED,{"item":"abc"}
2,ORDER_PLACED,{"item":"xyz"}
```

---

### Test 2 — Full end-to-end pipeline test

**Step 1 — Upload CSV to S3 (triggers Lambda automatically)**

```bash
aws s3 cp test_events.csv s3://poc-ecs-sqs-input-022784798356/test_events.csv --region us-east-1
```

**Step 2 — Confirm Lambda chunker fired (~15–20s after upload)**

```bash
aws logs tail /aws/lambda/poc-ecs-sqs-chunker --region us-east-1
```

Expected output:
```
START RequestId: ...
END RequestId: ...
REPORT ... Duration: ~250ms
```

**Step 3 — Confirm SQS received chunk messages**

```bash
aws sqs get-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/022784798356/poc-ecs-sqs-queue \
  --attribute-names ApproximateNumberOfMessages \
  --region us-east-1
```

Expected: `ApproximateNumberOfMessages` = number of chunks (e.g. 3 for a 3000-row file)

**Step 4 — Scale out ECS workers to process the queue**

> CloudWatch alarms take ~5 min to trigger auto-scaling. Use this to test immediately:

```bash
aws ecs update-service \
  --cluster poc-ecs-sqs-cluster \
  --service poc-ecs-sqs-service \
  --desired-count 2 \
  --region us-east-1
```

**Step 5 — Watch workers process chunks (live)**

```bash
aws logs tail /ecs/poc-ecs-sqs --follow --region us-east-1
```

Expected output per worker per chunk:
```
Worker started, polling queue...
Pulled 3 message(s) from queue
Processed 1000 records from test_events.csv [29-40815]
Processed 1000 records from test_events.csv [40815-83815]
Processed 1000 records from test_events.csv [83815-126815]
```

**Step 6 — Confirm queue drained and DLQ is empty**

```bash
# Main queue — expect 0
aws sqs get-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/022784798356/poc-ecs-sqs-queue \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible \
  --region us-east-1

# DLQ — expect 0 (any value here means a chunk failed 3 times)
aws sqs get-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/022784798356/poc-ecs-sqs-dlq \
  --attribute-names ApproximateNumberOfMessages \
  --region us-east-1
```

---

### Test 3 — Invoke Lambda directly (bypass S3 trigger)

Useful for testing the chunker in isolation without uploading a new file:

```bash
aws lambda invoke \
  --function-name poc-ecs-sqs-chunker \
  --region us-east-1 \
  --cli-binary-format raw-in-base64-out \
  --payload '{"Records":[{"s3":{"bucket":{"name":"poc-ecs-sqs-input-022784798356"},"object":{"key":"test_events.csv"}}}]}' \
  response.json && type response.json
```

Expected: `null` (no error). Then check SQS for new messages.

---

### Test 4 — Scale-to-zero test

Verify ECS scales back to 0 after the queue drains:

**Step 1 — Wait for queue to empty, then check alarm state**

```bash
aws cloudwatch describe-alarms \
  --alarm-names poc-ecs-sqs-scale-in \
  --query "MetricAlarms[0].{State:StateValue,Reason:StateReason}" \
  --region us-east-1
```

Expected after ~2 minutes of empty queue: `State: ALARM`

**Step 2 — Confirm ECS scaled to 0**

```bash
aws ecs describe-services \
  --cluster poc-ecs-sqs-cluster \
  --services poc-ecs-sqs-service \
  --region us-east-1 \
  --query "services[0].{desired:desiredCount,running:runningCount,pending:pendingCount}"
```

Expected: `desired: 0, running: 0, pending: 0`

**Step 3 — Check auto-scaling activity log**

```bash
aws application-autoscaling describe-scaling-activities \
  --service-namespace ecs \
  --resource-id service/poc-ecs-sqs-cluster/poc-ecs-sqs-service \
  --region us-east-1 \
  --query "ScalingActivities[0:5].{Status:StatusCode,Cause:Cause,Time:StartTime}"
```

---

### Test 5 — DLQ / failure test

Verify failed chunks are dead-lettered after 3 retries:

**Step 1 — Send a deliberately malformed message**

```bash
aws sqs send-message \
  --queue-url https://sqs.us-east-1.amazonaws.com/022784798356/poc-ecs-sqs-queue \
  --message-body '{"s3_bucket":"poc-ecs-sqs-input-022784798356","s3_key":"nonexistent.csv","byte_start":0,"byte_end":100,"header_end":50}' \
  --region us-east-1
```

**Step 2 — Scale out a worker to pick it up**

```bash
aws ecs update-service \
  --cluster poc-ecs-sqs-cluster \
  --service poc-ecs-sqs-service \
  --desired-count 1 \
  --region us-east-1
```

**Step 3 — Watch it fail in worker logs**

```bash
aws logs tail /ecs/poc-ecs-sqs --follow --region us-east-1
```

Expected: `ERROR Failed to process message ...: ...` repeated 3 times

**Step 4 — Confirm message moved to DLQ**

```bash
aws sqs get-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/022784798356/poc-ecs-sqs-dlq \
  --attribute-names ApproximateNumberOfMessages \
  --region us-east-1
```

Expected: `ApproximateNumberOfMessages: 1`

**Step 5 — Purge DLQ after investigation**

```bash
aws sqs purge-queue \
  --queue-url https://sqs.us-east-1.amazonaws.com/022784798356/poc-ecs-sqs-dlq \
  --region us-east-1
```

---

### Test 6 — Large file test

Verify the pipeline handles files larger than Lambda memory (256MB limit):

```bash
# Generate a 100,000-row file (100 chunks)
py -c "
lines = ['event_id,event_type,payload']
for i in range(1, 100001):
    lines.append(f'{i},ORDER_PLACED,{{\"item\":\"product_{i}\"}}') 
open('large_test.csv', 'w').write('\n'.join(lines) + '\n')
print('done')
"

aws s3 cp large_test.csv s3://poc-ecs-sqs-input-022784798356/ --region us-east-1
```

Then scale out workers and confirm all 100 chunks are processed:

```bash
aws ecs update-service \
  --cluster poc-ecs-sqs-cluster \
  --service poc-ecs-sqs-service \
  --desired-count 5 \
  --region us-east-1
```

---

### Monitoring Commands (quick reference)

```bash
# ECS task counts
aws ecs describe-services \
  --cluster poc-ecs-sqs-cluster \
  --services poc-ecs-sqs-service \
  --region us-east-1 \
  --query "services[0].{desired:desiredCount,running:runningCount,pending:pendingCount}"

# SQS queue depth
aws sqs get-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/022784798356/poc-ecs-sqs-queue \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible \
  --region us-east-1

# DLQ depth
aws sqs get-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/022784798356/poc-ecs-sqs-dlq \
  --attribute-names ApproximateNumberOfMessages \
  --region us-east-1

# Lambda logs
aws logs tail /aws/lambda/poc-ecs-sqs-chunker --follow --region us-east-1

# Worker logs (live)
aws logs tail /ecs/poc-ecs-sqs --follow --region us-east-1

# Scale-out alarm
aws cloudwatch describe-alarms \
  --alarm-names poc-ecs-sqs-scale-out \
  --query "MetricAlarms[0].{State:StateValue,Reason:StateReason}" \
  --region us-east-1

# Scale-in alarm
aws cloudwatch describe-alarms \
  --alarm-names poc-ecs-sqs-scale-in \
  --query "MetricAlarms[0].{State:StateValue,Reason:StateReason}" \
  --region us-east-1

# Auto-scaling activity
aws application-autoscaling describe-scaling-activities \
  --service-namespace ecs \
  --resource-id service/poc-ecs-sqs-cluster/poc-ecs-sqs-service \
  --region us-east-1 \
  --query "ScalingActivities[0:5].{Status:StatusCode,Cause:Cause,Time:StartTime}"

# Manually scale to zero
aws ecs update-service \
  --cluster poc-ecs-sqs-cluster \
  --service poc-ecs-sqs-service \
  --desired-count 0 \
  --region us-east-1

# Purge main queue (reset between tests)
aws sqs purge-queue \
  --queue-url https://sqs.us-east-1.amazonaws.com/022784798356/poc-ecs-sqs-queue \
  --region us-east-1
```

---

## Scaling Logic

| ApproximateNumberOfMessagesVisible | ECS Tasks |
|---|---|
| 0 | 0 (scale to zero) |
| 1–10 | 1 |
| 11–50 | 3 |
| 51+ | 5 (up to max 10) |

Cooldown: 60s scale-out, 120s scale-in (2 consecutive 0-message periods).

---

## Chunking Logic

| Setting | Value |
|---|---|
| Trigger | S3 `ObjectCreated` on `.csv` files |
| Lines per chunk | 1000 (configurable via `LINES_PER_CHUNK` Lambda env var) |
| Chunk format | S3 byte range descriptor (header + data range) |
| DLQ retries | 3 attempts before dead-lettering |
| Lambda timeout | 15 minutes (supports large files) |

Workers perform two S3 Range reads per message — one for the header row, one for the assigned chunk — then parse and process each row as a business event.

---

## Known Behaviours

| Behaviour | Explanation |
|---|---|
| Alarm stuck in `INSUFFICIENT_DATA` after file drop | SQS publishes metrics to CloudWatch every ~5 min. Use manual `update-service` to test immediately. |
| `python` not found on Windows | Use `py inject.py ...` instead of `python inject.py ...` |
| PowerShell strips single quotes from JSON args | Use `aws ecs update-service --desired-count N` instead of complex JSON policy commands in PowerShell |
| Tasks pending for 60–90s after scale-out | Normal Fargate cold start time for image pull + container init |
| Lambda streams large files | Lambda never loads the full file into memory — it streams line by line, safe for files larger than Lambda memory |

---

## Tear Down

```bash
# Empty the S3 bucket first (required before destroy)
aws s3 rm s3://poc-ecs-sqs-input-022784798356 --recursive

cd terraform
terraform destroy -var="container_image=dummy"

# Delete ECR repo
aws ecr delete-repository --repository-name poc-ecs-sqs-worker --force --region us-east-1
```
