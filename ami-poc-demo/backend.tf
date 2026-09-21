terraform {
  backend "s3" {
    bucket         = "your-existing-terraform-state-bucket"
    key            = "ami-poc-demo/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "your-existing-terraform-lock-table"
    encrypt        = true
  }
}
