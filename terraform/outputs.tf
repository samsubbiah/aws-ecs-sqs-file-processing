output "sqs_queue_url" {
  value = aws_sqs_queue.worker.url
}

output "sqs_queue_name" {
  value = aws_sqs_queue.worker.name
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  value = aws_ecs_service.worker.name
}
