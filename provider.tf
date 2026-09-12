terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Optional but recommended: remote state so Terraform's state file
  # isn't lost between GitHub Actions runs (each run starts fresh).
  # Uncomment and fill in once you've created an S3 bucket (and
  # optionally a DynamoDB table for state locking).
  #
  # backend "s3" {
  #   bucket         = "your-terraform-state-bucket"
  #   key            = "ec2/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "terraform-locks"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.aws_region
}
