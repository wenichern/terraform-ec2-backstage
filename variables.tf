variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t2.micro"
}

variable "key_name" {
  description = "Name of an existing EC2 key pair for SSH access (leave blank to disable SSH key)"
  type        = string
  default     = ""
}

variable "instance_name" {
  description = "Value for the Name tag on the instance"
  type        = string
  default     = "terraform-ec2-demo"
}

variable "allowed_ssh_cidr" {
  description = "CIDR block allowed to SSH into the instance"
  type        = string
  default     = "0.0.0.0/0" # tighten this to your own IP, e.g. 1.2.3.4/32
}
