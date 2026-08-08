import boto3, json, os

sqs = boto3.client("sqs")
s3  = boto3.client("s3")

QUEUE_URL       = os.environ["QUEUE_URL"]
LINES_PER_CHUNK = int(os.environ.get("LINES_PER_CHUNK", 1000))


def handler(event, context):
    record = event["Records"][0]
    bucket = record["s3"]["bucket"]["name"]
    key    = record["s3"]["object"]["key"]

    stream     = s3.get_object(Bucket=bucket, Key=key)["Body"]
    entries    = []
    chunk_id   = 0
    byte_pos   = 0
    chunk_start = 0
    line_count  = 0
    header_end  = None

    for raw_line in stream.iter_lines():
        line_len = len(raw_line) + 1  # +1 for \n

        if header_end is None:
            header_end = byte_pos + line_len
            byte_pos   = header_end
            continue

        if line_count == 0:
            chunk_start = byte_pos

        line_count += 1
        byte_pos   += line_len

        if line_count == LINES_PER_CHUNK:
            entries, chunk_id = _enqueue(entries, chunk_id, bucket, key, chunk_start, byte_pos, header_end)
            line_count = 0

    if line_count > 0:
        entries, chunk_id = _enqueue(entries, chunk_id, bucket, key, chunk_start, byte_pos, header_end)

    if entries:
        sqs.send_message_batch(QueueUrl=QUEUE_URL, Entries=entries)


def _enqueue(entries, chunk_id, bucket, key, byte_start, byte_end, header_end):
    entries.append({
        "Id": str(chunk_id),
        "MessageBody": json.dumps({
            "s3_bucket":  bucket,
            "s3_key":     key,
            "byte_start": byte_start,
            "byte_end":   byte_end,
            "header_end": header_end,
        }),
    })
    if len(entries) == 10:
        sqs.send_message_batch(QueueUrl=QUEUE_URL, Entries=entries)
        entries = []
    return entries, chunk_id + 1
