# Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                  AWS Cloud                                       │
│                                                                                  │
│   ┌──────────────┐    S3 Event       ┌──────────────┐                           │
│   │   S3 Bucket  │──────────────────►│    Lambda    │                           │
│   │  (input/)    │   ObjectCreated   │   Chunker    │                           │
│   │  *.csv drop  │                   │              │                           │
│   └──────────────┘                   │ - streams S3 │                           │
│                                      │ - splits by  │                           │
│                                      │   line count │                           │
│                                      │ - sends chunk│                           │
│                                      │   descriptors│                           │
│                                      └──────┬───────┘                           │
│                                             │ SendMessageBatch                  │
│                                             │ {s3_bucket, s3_key,               │
│                                             │  byte_start, byte_end,            │
│                                             │  header_end}                      │
│                                             ▼                                   │
│                                      ┌──────────────┐                           │
│                                      │     SQS      │                           │
│                                      │    Queue     │◄── DLQ (3 retries)        │
│                                      └──────┬───────┘                           │
│                                             │                                   │
│                          ┌──────────────────┼──────────────────┐                │
│                          │ Metrics          │                  │                │
│                          ▼                  ▼                  │                │
│                   ┌──────────────┐   ┌──────────────────────────────────────┐  │
│                   │  CloudWatch  │   │               VPC                    │  │
│                   │    Alarm     │   │                                      │  │
│                   │              │   │  ┌───────────────────────────────┐   │  │
│                   │ Threshold:   │   │  │    ECS Cluster (Fargate)      │   │  │
│                   │  msgs == 0   │   │  │                               │   │  │
│                   └──────┬───────┘   │  │  ┌────────┐ ┌────────┐ ┌───┐ │   │  │
│                          │           │  │  │Worker 1│ │Worker 2│ │ N │ │   │  │
│                          │           │  │  │        │ │        │ │   │ │   │  │
│                          │           │  │  │1. Poll │ │1. Poll │ │...│ │   │  │
│                          │           │  │  │2. S3   │ │2. S3   │ │   │ │   │  │
│                          │           │  │  │  Range │ │  Range │ │   │ │   │  │
│                          │           │  │  │  read  │ │  read  │ │   │ │   │  │
│                          │           │  │  │3. Parse│ │3. Parse│ │   │ │   │  │
│                          │           │  │  │  rows  │ │  rows  │ │   │ │   │  │
│                          │           │  │  │4.Delete│ │4.Delete│ │   │ │   │  │
│                          │           │  │  └────────┘ └────────┘ └───┘ │   │  │
│                          │           │  └───────────────────────────────┘   │  │
│                          │           │                  ▲                    │  │
│                          │           │                  │ Scale Out/In       │  │
│                          │           │  ┌───────────────┴───────────────┐   │  │
│                          └───────────┼─►│      App Auto Scaling         │   │  │
│                                      │  │  Scale-Out: 60s cooldown      │   │  │
│                                      │  │  Scale-In:  120s (2 periods)  │   │  │
│                                      │  └───────────────────────────────┘   │  │
│                                      └──────────────────────────────────────┘  │
│                                                                                  │
│   ┌──────────────┐                                                               │
│   │     ECR      │  Docker Image (pulled by ECS tasks at startup)                │
│   │  Repository  │                                                               │
│   └──────────────┘                                                               │
│                                                                                  │
│   ┌──────────────┐                                                               │
│   │     IAM      │  Task Role      → SQS (Receive, Delete) + S3 (GetObject)     │
│   │    Roles     │  Execution Role → ECR pull, CloudWatch Logs                  │
│   │              │  Lambda Role    → S3 (GetObject) + SQS (SendMessage)         │
│   └──────────────┘                                                               │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Scaling Table

| ApproximateNumberOfMessagesVisible | ECS Tasks |
|---|---|
| 0 | 0 — scale to zero |
| 1 – 10 | 1 |
| 11 – 50 | 3 |
| 51+ | 5 (max 10) |

## Flow

1. **S3** — a `.csv` file is dropped into the input bucket
2. **Lambda Chunker** — triggered by S3 event, streams the file, tracks byte offsets at newline boundaries, sends chunk descriptor messages to SQS (`s3_bucket`, `s3_key`, `byte_start`, `byte_end`, `header_end`)
3. **CloudWatch** detects `ApproximateNumberOfMessagesVisible > 0` → App Auto Scaling triggers scale-out
4. **ECS Fargate Workers** start, each polls SQS, fetches its assigned chunk via S3 Range read, parses CSV rows, processes each business event, deletes the message
5. Queue drains → CloudWatch alarm fires (`msgs == 0` for 2 consecutive periods) → ECS scales back to **0 tasks**
6. **DLQ** catches any chunk that fails 3 consecutive processing attempts

## SQS Message Format

```json
{
  "s3_bucket":  "poc-ecs-sqs-input-<account-id>",
  "s3_key":     "uploads/events.csv",
  "byte_start": 102400,
  "byte_end":   204800,
  "header_end": 102
}
```

Workers perform two S3 Range reads — one for the header row, one for their assigned chunk — then parse and process each row independently.
