import boto3
import argparse
import json
import time

def inject(queue_url: str, count: int, region: str):
    sqs = boto3.client("sqs", region_name=region)
    batch, sent = [], 0

    for i in range(1, count + 1):
        batch.append({
            "Id": str(i),
            "MessageBody": json.dumps({"job_id": i, "payload": f"task-{i}"})
        })
        if len(batch) == 10 or i == count:
            sqs.send_message_batch(QueueUrl=queue_url, Entries=batch)
            sent += len(batch)
            print(f"Sent {sent}/{count} messages")
            batch = []
            time.sleep(0.1)

    print(f"Done. Total sent: {sent}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inject messages into SQS")
    parser.add_argument("--queue-url", required=True, help="SQS Queue URL")
    parser.add_argument("--count", type=int, default=20, help="Number of messages to send")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    args = parser.parse_args()

    inject(args.queue_url, args.count, args.region)
