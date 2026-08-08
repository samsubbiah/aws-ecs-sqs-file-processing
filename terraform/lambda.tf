data "archive_file" "chunker" {
  type        = "zip"
  source_file = "${path.module}/../lambda/chunker.py"
  output_path = "${path.module}/../lambda/chunker.zip"
}

resource "aws_lambda_function" "chunker" {
  function_name    = "${var.project}-chunker"
  filename         = data.archive_file.chunker.output_path
  source_code_hash = data.archive_file.chunker.output_base64sha256
  handler          = "chunker.handler"
  runtime          = "python3.12"
  timeout          = 900  # 15 min for large files
  memory_size      = 256
  role             = aws_iam_role.lambda_chunker.arn

  environment {
    variables = {
      QUEUE_URL       = aws_sqs_queue.worker.url
      LINES_PER_CHUNK = "1000"
    }
  }

  depends_on = [aws_cloudwatch_log_group.chunker]
}

resource "aws_cloudwatch_log_group" "chunker" {
  name              = "/aws/lambda/${var.project}-chunker"
  retention_in_days = 7
}

resource "aws_lambda_permission" "s3_invoke" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.chunker.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.input.arn
}
