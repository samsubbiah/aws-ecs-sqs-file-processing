resource "aws_s3_bucket" "input" {
  bucket        = "${var.project}-input-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
  tags          = { Name = var.project }
}

resource "aws_s3_bucket_notification" "input" {
  bucket = aws_s3_bucket.input.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.chunker.arn
    events              = ["s3:ObjectCreated:*"]
    filter_suffix       = ".csv"
  }

  depends_on = [aws_lambda_permission.s3_invoke]
}

data "aws_caller_identity" "current" {}
