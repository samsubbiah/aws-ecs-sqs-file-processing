# SQS Queue
resource "aws_sqs_queue" "worker" {
  name                       = "${var.project}-queue"
  visibility_timeout_seconds = 60
  tags                       = { Name = var.project }
}
