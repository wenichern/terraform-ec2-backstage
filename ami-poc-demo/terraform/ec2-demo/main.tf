# Demo workload for the AMI-upgrade PoC.
# The "fix" in the golden path is a one-line change: ami_id. Changing the AMI of an
# aws_instance REPLACES the instance (brief downtime). Fine for a PoC; for real
# workloads use a launch template + Auto Scaling group with instance refresh.

terraform {
  required_version = ">= 1.10"

  # Partial config: bucket/key/region are passed with -backend-config (see workflow)
  backend "s3" {
    use_lockfile = true
  }
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

variable "region"            { default = "us-east-1" }
variable "ami_id"            { type = string }
variable "instance_type"     { default = "t3.micro" }
variable "name"              { default = "ami-poc-demo" }
variable "backstage_entity"  {
  description = "Backstage catalog entity ref that owns this instance"
  default     = "component:default/ami-poc-demo"
}

variable "alarm_topic_arn" {
  description = "SNS topic from ../detect-platform (output sns_topic_arn); empty = no notification"
  default     = ""
}

provider "aws" {
  region = var.region
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_instance" "app" {
  ami           = var.ami_id
  instance_type = var.instance_type
  subnet_id     = data.aws_subnets.default.ids[0]

  metadata_options {
    http_tokens = "required" # IMDSv2 only
  }

  tags = {
    Name               = var.name
    "backstage-entity" = var.backstage_entity # read by the AMI checker and Backstage
    ManagedBy          = "terraform"
  }
}

# Fires when the checker reports AmiStatus >= 1 (newer AMI available, or AMI deprecated).
# Stable name and alarm follow the instance: after the golden-path replaces the
# instance, this alarm is updated in place and returns to OK on the next checker run.
resource "aws_cloudwatch_metric_alarm" "ami_drift" {
  alarm_name          = "ami-drift-${var.name}"
  alarm_description   = "EC2 ${var.name} (${var.backstage_entity}) is not on the latest AMI or its AMI is deprecated"
  namespace           = "SRE/AmiDrift"
  metric_name         = "AmiStatus"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    InstanceId = aws_instance.app.id
    Service    = var.backstage_entity
  }

  alarm_actions = var.alarm_topic_arn == "" ? [] : [var.alarm_topic_arn]
  ok_actions    = var.alarm_topic_arn == "" ? [] : [var.alarm_topic_arn]

  tags = {
    "backstage-entity" = var.backstage_entity
  }
}

output "instance_id" { value = aws_instance.app.id }
output "ami_id"      { value = aws_instance.app.ami }
output "alarm_name"  { value = aws_cloudwatch_metric_alarm.ami_drift.alarm_name }
