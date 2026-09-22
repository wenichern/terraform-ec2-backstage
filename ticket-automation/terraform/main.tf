# Ticket-automation Lambda: subscribes to the SAME SNS topic the CloudWatch alarms
# already notify (ami-drift-alerts, output by ../ami-poc-demo/terraform/detect-platform).
# Apply this AFTER detect-platform exists, passing its sns_topic_arn as a variable
# (see README.md for the exact command).
#
# NOTE: uses the same S3 backend pattern as the other stacks (partial config via
# -backend-config). Not applied here - no AWS/Terraform available in this sandbox.
# Run `terraform validate` and review `plan` before applying.

terraform {
  required_version = ">= 1.10"

  backend "s3" {
    use_lockfile = true
  }

  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 5.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.4" }
  }
}

variable "region" {
  default = "us-east-1"
}

variable "sns_topic_arn" {
  description = "Output from ../ami-poc-demo/terraform/detect-platform (sns_topic_arn)"
  type        = string
}

# Leave unset to use the built-in MockServiceNowClient (creates/closes in-memory
# "tickets" that vanish on the next cold start - fine for proving the flow works,
# useless as a real record). Set both once a real ServiceNow PDI exists.
variable "servicenow_instance_url" {
  default = ""
}
variable "servicenow_user" {
  default   = ""
  sensitive = true
}
variable "servicenow_password" {
  default   = ""
  sensitive = true
}

provider "aws" {
  region = var.region
}

data "archive_file" "ticket_automation" {
  type        = "zip"
  output_path = "${path.module}/build/ticket_automation.zip"

  source {
    content  = file("${path.module}/../servicenow_client.py")
    filename = "servicenow_client.py"
  }
  source {
    content  = file("${path.module}/../lambda_handler.py")
    filename = "lambda_handler.py"
  }
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ticket_automation" {
  name               = "ami-ticket-automation"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.ticket_automation.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "ticket_automation" {
  function_name    = "ami-ticket-automation"
  role             = aws_iam_role.ticket_automation.arn
  runtime          = "python3.12"
  handler          = "lambda_handler.handler"
  filename         = data.archive_file.ticket_automation.output_path
  source_code_hash = data.archive_file.ticket_automation.output_base64sha256
  timeout          = 30
  memory_size      = 128 # stdlib-only, no heavy deps

  environment {
    variables = {
      SERVICENOW_INSTANCE_URL = var.servicenow_instance_url
      SERVICENOW_USER         = var.servicenow_user
      SERVICENOW_PASSWORD     = var.servicenow_password
    }
  }
}

resource "aws_sns_topic_subscription" "ticket_automation" {
  topic_arn = var.sns_topic_arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.ticket_automation.arn
}

resource "aws_lambda_permission" "sns" {
  statement_id  = "AllowSNS"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ticket_automation.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = var.sns_topic_arn
}

output "function_name" {
  value = aws_lambda_function.ticket_automation.function_name
}
