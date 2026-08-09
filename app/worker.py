import boto3
import os
import time
import json
import csv
import io
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

sqs = boto3.client("sqs", region_name=os.environ["AWS_REGION"])
s3  = boto3.client("s3",  region_name=os.environ["AWS_REGION"])

QUEUE_URL = os.environ["SQS_QUEUE_URL"]


def process_chunk(msg):
    body = json.loads(msg["Body"])
    bucket     = body["s3_bucket"]
    key        = body["s3_key"]
    byte_start = body["byte_start"]
    byte_end   = body["byte_end"]
    header_end = body["header_end"]

    header = s3.get_object(
        Bucket=bucket, Key=key,
        Range=f"bytes=0-{header_end - 1}"
    )["Body"].read().decode()

    chunk = s3.get_object(
        Bucket=bucket, Key=key,
        Range=f"bytes={byte_start}-{byte_end - 1}"
    )["Body"].read().decode()

    combined = (header + chunk).replace('\r\n', '\n').replace('\r', '\n')
    reader = csv.DictReader(io.StringIO(combined))
    count = 0
    for row in reader:
        _process_row(row)
        count += 1

    log.info(f"Processed {count} records from {key} [{byte_start}-{byte_end}]")


def _process_row(row):
    time.sleep(0.001)  # replace with real business logic


def main():
    log.info("Worker started, polling queue...")
    while True:
        response = sqs.receive_message(
            QueueUrl=QUEUE_URL,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=20,
        )
        messages = response.get("Messages", [])
        log.info(f"Pulled {len(messages)} message(s) from queue")
        if not messages:
            continue

        for msg in messages:
            try:
                process_chunk(msg)
                sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=msg["ReceiptHandle"])
            except Exception as e:
                log.error(f"Failed to process message {msg['MessageId']}: {e}")


if __name__ == "__main__":
    main()
