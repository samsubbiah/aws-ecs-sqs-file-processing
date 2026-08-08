# POC: ECS Scale to Zero based on SQS

## Architecture

See full diagram → [architecture.md](./architecture.md)

```
 Injector ──► SQS Queue ──► CloudWatch Alarm ──► App Auto Scaling
                  ▲                                      │
                  │         ECS Fargate Cluster          │
                  │    ┌──────────────────────────┐      │
                  └────│  Worker Tasks (0 → N)    │◄─────┘
                       │  poll → process → delete │
                       └──────────────────────────┘
```

- Workers poll SQS, process messages, delete them
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
| ECR Repository | `poc-ecs-sqs-worker` |
| SQS Queue | `poc-ecs-sqs-queue` |
| SQS Queue URL | `https://sqs.us-east-1.amazonaws.com/022784798356/poc-ecs-sqs-queue` |
| ECS Cluster | `poc-ecs-sqs-cluster` |
| ECS Service | `poc-ecs-sqs-service` |
| CloudWatch Log Group | `/ecs/poc-ecs-sqs` (retention: 7 days) |
| CloudWatch Alarm (scale-out) | `poc-ecs-sqs-scale-out` |
| CloudWatch Alarm (scale-in) | `poc-ecs-sqs-scale-in` |
| IAM Task Role | `poc-ecs-sqs-task-role` |
| IAM Execution Role | `poc-ecs-sqs-exec-role` |
| VPC | `poc-ecs-sqs-vpc` (10.0.0.0/16) |

---

## Step 1 — Build & Push Docker Image

```bash
# Set your account/region
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

> On Windows PowerShell, `python` may not be in PATH — use `py` instead (see Step 3).

---

## Step 2 — Deploy Terraform

```bash
cd terraform
terraform init
terraform apply -var="container_image=$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/poc-ecs-sqs-worker:latest"
```

Terraform creates 22 resources: VPC, subnets, IGW, SQS, ECS cluster/service/task definition, IAM roles, CloudWatch alarms, App Auto Scaling target and policies.

Note the `sqs_queue_url` from the output.

---

## Step 3 — Inject Messages (triggers scale out)

```bash
cd injector

# Windows — use py launcher
py -m pip install -r requirements.txt
py inject.py --queue-url https://sqs.us-east-1.amazonaws.com/022784798356/poc-ecs-sqs-queue --count 50 --region us-east-1

# Linux/macOS
pip install -r requirements.txt
python inject.py --queue-url <sqs_queue_url> --count 50 --region us-east-1
```

Expected output:
```
Sent 10/50 messages
Sent 20/50 messages
...
Done. Total sent: 50
```

---

## Step 4 — Observe Scale to Zero

Once all messages are processed, the CloudWatch alarm fires after ~2 minutes (2 consecutive 0-message periods) and ECS scales back to 0 tasks.

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
# SQS metrics take up to 5 min to appear in CloudWatch.
# Use this to force tasks to start immediately for testing.
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

## Known Behaviours

| Behaviour | Explanation |
|---|---|
| Alarm stuck in `INSUFFICIENT_DATA` after inject | SQS publishes metrics to CloudWatch every ~5 min. Use manual `update-service` to test immediately. |
| `python` not found on Windows | Use `py inject.py ...` instead of `python inject.py ...` |
| PowerShell strips single quotes from JSON args | Use `aws ecs update-service --desired-count N` instead of complex JSON policy commands in PowerShell |
| Tasks pending for 60–90s after scale-out | Normal Fargate cold start time for image pull + container init |

---

## Tear Down

```bash
cd terraform
terraform destroy -var="container_image=dummy"

# Delete ECR repo
aws ecr delete-repository --repository-name poc-ecs-sqs-worker --force --region us-east-1
```
