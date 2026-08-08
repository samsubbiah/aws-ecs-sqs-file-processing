output "sqs_queue_url" {
  value = aws_sqs_queue.worker.url
}

output "sqs_queue_name" {
  value = aws_sqs_queue.worker.name
}

output "sqs_dlq_url" {
  value = aws_sqs_queue.dlq.url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  value = aws_ecs_service.worker.name
}

output "s3_input_bucket" {
  value = aws_s3_bucket.input.bucket
}

output "lambda_chunker_name" {
  value = aws_lambda_function.chunker.function_name
}
