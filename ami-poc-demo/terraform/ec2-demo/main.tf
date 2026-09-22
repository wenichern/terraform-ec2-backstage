# ASG + launch template version of the PoC demo (replaces aws_instance.app).
# The "fix" becomes: update ami_id -> launch template new version -> instance refresh.
# No more replace-in-place downtime on a single instance; ASG keeps min_size healthy
# throughout the refresh.

terraform {
  required_version = ">= 1.10"

  backend "s3" {
    use_lockfile = true
  }

  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

variable "region"           { default = "us-east-1" }
variable "ami_id"           { type = string }
variable "instance_type"    { default = "t3.micro" }
variable "name"             { default = "ami-poc-demo" }
variable "desired_capacity" { default = 1 }
variable "min_size"         { default = 1 }
variable "max_size"         { default = 2 }  # >min_size so instance refresh has room to roll
variable "backstage_entity" {
  default = "component:default/ami-poc-demo"
}
variable "alarm_topic_arn" {
  default = ""
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
  filter {
    name   = "availability-zone"
    values = ["us-east-1a", "us-east-1b", "us-east-1c", "us-east-1d", "us-east-1f"] # excludes 1e: no t3.micro
  }
}

# Purpose-built SG instead of relying on the VPC default (no inbound needed).
resource "aws_security_group" "app" {
  name        = "${var.name}-sg"
  description = "AMI PoC demo instance - no inbound, outbound only"
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { "backstage-entity" = var.backstage_entity }
}

resource "aws_launch_template" "app" {
  name_prefix   = "${var.name}-"
  image_id      = var.ami_id
  instance_type = var.instance_type

  vpc_security_group_ids = [aws_security_group.app.id]

  metadata_options {
    http_tokens = "required" # IMDSv2 only
  }

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name               = var.name
      "backstage-entity" = var.backstage_entity
      ManagedBy          = "terraform"
    }
  }

  # New version on every ami_id change; ASG below picks it up via instance refresh.
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_autoscaling_group" "app" {
  name                = var.name
  desired_capacity    = var.desired_capacity
  min_size            = var.min_size
  max_size            = var.max_size
  vpc_zone_identifier = data.aws_subnets.default.ids
  health_check_type   = "EC2"

  launch_template {
    id      = aws_launch_template.app.id
    version = aws_launch_template.app.latest_version
  }

  # The actual "fix" mechanism: when the launch template version changes (new ami_id),
  # roll the fleet keeping at least 90% healthy throughout. No manual instance
  # termination needed - just change ami_id and apply.
  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 90
      instance_warmup        = 60
    }
  }

  tag {
    key                 = "Name"
    value               = var.name
    propagate_at_launch = true
  }
  tag {
    key                 = "backstage-entity"
    value               = var.backstage_entity
    propagate_at_launch = true
  }

  lifecycle {
    ignore_changes = [desired_capacity] # let manual/future autoscaling policies own this
  }
}

# One alarm per ASG (not per instance): fires if ANY instance in the group drifts.
# The checker already tags results by instance+service; this alarm just says "look".
resource "aws_cloudwatch_metric_alarm" "ami_drift" {
  alarm_name          = "ami-drift-${var.name}"
  alarm_description   = "One or more instances in ASG ${var.name} (${var.backstage_entity}) are not on the latest AMI or its AMI is deprecated"
  namespace           = "SRE/AmiDrift"
  metric_name         = "AmiStatus"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    Service = var.backstage_entity  # aggregate across all instances tagged with this service
  }

  alarm_actions = var.alarm_topic_arn == "" ? [] : [var.alarm_topic_arn]
  ok_actions    = var.alarm_topic_arn == "" ? [] : [var.alarm_topic_arn]

  tags = { "backstage-entity" = var.backstage_entity }
}

output "asg_name"          { value = aws_autoscaling_group.app.name }
output "launch_template_id" { value = aws_launch_template.app.id }
output "alarm_name"        { value = aws_cloudwatch_metric_alarm.ami_drift.alarm_name }
