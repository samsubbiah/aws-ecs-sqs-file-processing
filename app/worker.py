import boto3
import os
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

sqs = boto3.client("sqs", region_name=os.environ["AWS_REGION"])
QUEUE_URL = os.environ["SQS_QUEUE_URL"]


def process_message(msg):
    log.info(f"Processing: {msg['Body']}")
    time.sleep(2)  # simulate work
    log.info(f"Done: {msg['MessageId']}")


def main():
    log.info("Worker started, polling queue...")
    while True:
        response = sqs.receive_message(
            QueueUrl=QUEUE_URL,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=20,  # long polling
        )
        messages = response.get("Messages", [])
        log.info(f"Pulled {len(messages)} message(s) from queue")
        if not messages:
            continue

        for msg in messages:
            process_message(msg)
            sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=msg["ReceiptHandle"])


if __name__ == "__main__":
    main()
