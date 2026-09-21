# Shared detection platform (apply once per account/region):
# Lambda (checker) + EventBridge schedule + SNS topic. Per-service alarms live with
# each service's own Terraform (see ../ec2-demo) and point at this SNS topic.

terraform {
  required_version = ">= 1.10"

  # Partial config: bucket/key/region are passed with -backend-config (see workflow)
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

variable "schedule" {
  default = "rate(5 minutes)"
}

variable "latest_ami_ssm_param" {
  default = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

variable "alert_email" {
  description = "Optional: email address to subscribe to alarm notifications"
  default     = ""
}

provider "aws" {
  region = var.region
}

# ---- Notifications --------------------------------------------------------
resource "aws_sns_topic" "ami_alerts" {
  name = "ami-drift-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.ami_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ---- Lambda ---------------------------------------------------------------
data "archive_file" "checker" {
  type        = "zip"
  output_path = "${path.module}/build/checker.zip"

  source {
    content  = file("${path.module}/../../checker/ami_checker.py")
    filename = "ami_checker.py"
  }

  source {
    content  = file("${path.module}/../../checker/lambda_handler.py")
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

resource "aws_iam_role" "checker" {
  name               = "ami-checker"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.checker.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "checker" {
  statement {
    actions   = ["ec2:DescribeInstances", "ec2:DescribeImages"]
    resources = ["*"]
  }

  statement {
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.region}::parameter${var.latest_ami_ssm_param}"]
  }

  statement {
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["SRE/AmiDrift"]
    }
  }
}

resource "aws_iam_role_policy" "checker" {
  name   = "ami-checker-read-and-publish"
  role   = aws_iam_role.checker.id
  policy = data.aws_iam_policy_document.checker.json
}

resource "aws_lambda_function" "checker" {
  function_name    = "ami-checker"
  role             = aws_iam_role.checker.arn
  runtime          = "python3.12"
  handler          = "lambda_handler.handler"
  filename         = data.archive_file.checker.output_path
  source_code_hash = data.archive_file.checker.output_base64sha256
  timeout          = 60
  memory_size      = 256

  environment {
    variables = {
      LATEST_AMI_SSM_PARAM = var.latest_ami_ssm_param
    }
  }
}

# ---- Schedule -------------------------------------------------------------
resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "ami-checker-schedule"
  schedule_expression = var.schedule
}

resource "aws_cloudwatch_event_target" "checker" {
  rule = aws_cloudwatch_event_rule.schedule.name
  arn  = aws_lambda_function.checker.arn
}

resource "aws_lambda_permission" "events" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.checker.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule.arn
}

output "sns_topic_arn" {
  value = aws_sns_topic.ami_alerts.arn
}

output "checker_function" {
  value = aws_lambda_function.checker.function_name
}
