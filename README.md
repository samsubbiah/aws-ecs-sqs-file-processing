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

## Testing & Monitoring Commands

### Verify AWS identity
```bash
aws sts get-caller-identity
```

### Check messages in queue
```bash
aws sqs get-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/022784798356/poc-ecs-sqs-queue \
  --attribute-names ApproximateNumberOfMessages \
  --region us-east-1
```

### Check DLQ for failed chunks
```bash
aws sqs get-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/022784798356/poc-ecs-sqs-dlq \
  --attribute-names ApproximateNumberOfMessages \
  --region us-east-1
```

### Check Lambda chunker logs
```bash
aws logs tail /aws/lambda/poc-ecs-sqs-chunker --follow --region us-east-1
```

### Check ECS task counts
```bash
aws ecs describe-services \
  --cluster poc-ecs-sqs-cluster \
  --services poc-ecs-sqs-service \
  --region us-east-1 \
  --query "services[0].{desired:desiredCount,running:runningCount,pending:pendingCount}"
```

### Check CloudWatch alarm state
```bash
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
```

### Check Auto Scaling activity
```bash
aws application-autoscaling describe-scaling-activities \
  --service-namespace ecs \
  --resource-id service/poc-ecs-sqs-cluster/poc-ecs-sqs-service \
  --region us-east-1 \
  --query "ScalingActivities[0:5].{Status:StatusCode,Cause:Cause,Time:StartTime}"
```

### Tail worker logs (live)
```bash
aws logs tail /ecs/poc-ecs-sqs --follow --region us-east-1
```

### Manually force scale out (bypass alarm wait)
```bash
aws ecs update-service \
  --cluster poc-ecs-sqs-cluster \
  --service poc-ecs-sqs-service \
  --desired-count 3 \
  --region us-east-1
```

### Manually scale back to zero
```bash
aws ecs update-service \
  --cluster poc-ecs-sqs-cluster \
  --service poc-ecs-sqs-service \
  --desired-count 0 \
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
