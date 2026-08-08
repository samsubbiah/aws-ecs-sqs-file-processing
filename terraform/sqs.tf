# Dead Letter Queue
resource "aws_sqs_queue" "dlq" {
  name = "${var.project}-dlq"
  tags = { Name = "${var.project}-dlq" }
}

# SQS Queue
resource "aws_sqs_queue" "worker" {
  name                       = "${var.project}-queue"
  visibility_timeout_seconds = 60
  tags                       = { Name = var.project }

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 3
  })
}
