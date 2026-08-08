# Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AWS Cloud                                       │
│                                                                              │
│   ┌──────────────┐     ┌──────────────────────────────────────────────────┐ │
│   │   Injector   │     │                    VPC                           │ │
│   │  (Python)    │     │                                                  │ │
│   │              │     │  ┌─────────────────────────────────────────────┐ │ │
│   │ inject.py    │     │  │           ECS Cluster (Fargate)             │ │ │
│   └──────┬───────┘     │  │                                             │ │ │
│          │             │  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  │ │ │
│          │ SendMessage │  │  │  Worker  │  │  Worker  │  │  Worker  │  │ │ │
│          │             │  │  │  Task 1  │  │  Task 2  │  │  Task N  │  │ │ │
│          ▼             │  │  └────┬─────┘  └────┬─────┘  └────┬─────┘  │ │ │
│   ┌──────────────┐     │  │       │              │              │        │ │ │
│   │     SQS      │◄────┼──┼───────┴──────────────┴──────────────┘        │ │ │
│   │    Queue     │     │  │         Poll / Delete Messages                │ │ │
│   └──────┬───────┘     │  └─────────────────────────────────────────────┘ │ │
│          │             │                        ▲                          │ │
│          │ Metrics     │                        │ Scale Out/In             │ │
│          ▼             │                        │                          │ │
│   ┌──────────────┐     │  ┌─────────────────────┴───────────────────────┐ │ │
│   │  CloudWatch  │     │  │          App Auto Scaling                   │ │ │
│   │    Alarm     ├─────┼─►│                                             │ │ │
│   │              │     │  │  Scale-Out: 60s cooldown                    │ │ │
│   │ Threshold:   │     │  │  Scale-In:  120s cooldown (2 periods)       │ │ │
│   │  msgs == 0   │     │  └─────────────────────────────────────────────┘ │ │
│   └──────────────┘     └──────────────────────────────────────────────────┘ │
│                                                                              │
│   ┌──────────────┐                                                           │
│   │     ECR      │  Docker Image                                             │
│   │  Repository  │─────────────────────────────────────────────────────►    │
│   └──────────────┘         (pulled by ECS tasks at startup)                 │
│                                                                              │
│   ┌──────────────┐                                                           │
│   │     IAM      │  Task Role → SQS (ReceiveMessage, DeleteMessage)          │
│   │    Roles     │  Execution Role → ECR pull, CloudWatch Logs               │
│   └──────────────┘                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Scaling Table

| ApproximateNumberOfMessagesVisible | ECS Tasks |
|---|---|
| 0 | 0 — scale to zero |
| 1 – 10 | 1 |
| 11 – 50 | 3 |
| 51+ | 5 (max 10) |

## Flow

1. **Injector** sends N messages to SQS
2. **CloudWatch** detects `ApproximateNumberOfMessagesVisible > 0` → alarm transitions to OK → App Auto Scaling triggers scale-out
3. **ECS Fargate** tasks start, poll SQS, process and delete messages
4. Queue drains → CloudWatch alarm fires (`msgs == 0` for 2 consecutive periods)
5. App Auto Scaling scales ECS service back to **0 tasks**
