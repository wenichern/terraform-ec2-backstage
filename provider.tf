terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket         = "wenichern-terraform-state-565393068778"
    key            = "ec2/terraform.tfstate"
    region         = "us-east-1"
    # dynamodb_table = "terraform-locks"
    use_lockfile = true
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region
}
