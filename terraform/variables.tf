variable "aws_region" {
  default = "us-east-1"
}

variable "project" {
  default = "poc-ecs-sqs"
}

variable "container_image" {
  description = "ECR image URI for the worker"
}
