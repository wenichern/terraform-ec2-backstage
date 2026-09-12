# Terraform EC2 Deploy (via GitHub Actions)

Deploys a single EC2 instance (`t2.micro`, `us-east-1` by default, Amazon Linux 2023) using
Terraform, automatically applied by a GitHub Actions workflow on every push to `main`.

## What's in here

```
.
├── main.tf                       # EC2 instance + security group + AMI lookup
├── variables.tf                  # Input variables (region, instance type, etc.)
├── outputs.tf                    # Instance ID / public IP / public DNS
├── provider.tf                   # AWS provider + Terraform version pin
├── terraform.tfvars.example      # Example variable values (copy to terraform.tfvars locally)
├── .gitignore
└── .github/workflows/terraform.yml  # CI: init, validate, plan, apply
```

## ⚠️ Important: state storage

This repo, as-is, uses **local Terraform state**. That works fine if you run
`terraform apply` from your own machine, but it will **not** work well purely through
GitHub Actions: every workflow run starts on a brand-new runner with no memory of previous
runs, so Terraform won't know an instance already exists and could try to create duplicates
on every push.

Before relying on GitHub Actions to manage this over time, set up a **remote backend**
(S3 bucket, optionally + a DynamoDB table for locking) and uncomment the `backend "s3"` block
in `provider.tf`. If you just want to try it once or twice, local state is fine to start.

## Setup steps

### 1. Create the GitHub repo
Create a new (empty) repository on GitHub, then from this folder:
```bash
git init
git add .
git commit -m "Initial commit: Terraform EC2 setup"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo>.git
git push -u origin main
```

### 2. Create an IAM user for GitHub Actions
In AWS, create an IAM user (or better, use OIDC — see note below) with permissions to manage
EC2 and security groups (e.g. `AmazonEC2FullAccess` for simplicity, or a scoped-down custom
policy for production). Generate an access key for it.

### 3. Add AWS credentials as GitHub secrets
In your repo: **Settings → Secrets and variables → Actions → New repository secret**
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

### 4. Push
Any push to `main` will trigger `.github/workflows/terraform.yml`, which runs
`terraform init`, `validate`, `plan`, and then `apply` (apply only runs on `main`, not on
pull requests — PRs just get a plan for review).

### 5. Check the outputs
After the workflow finishes, check the **Actions** tab → the run → the "Terraform Apply" step
log for the instance's public IP and DNS name (from `outputs.tf`).

## Customizing
Edit `variables.tf` defaults, or override them by creating a `terraform.tfvars` file locally
(it's gitignored) based on `terraform.tfvars.example`. To change values used in CI, either
edit the defaults in `variables.tf` or pass `-var` flags in the workflow.

## Security notes
- `allowed_ssh_cidr` defaults to `0.0.0.0/0` (open to the world). Change it to your own IP
  (e.g. `1.2.3.4/32`) before applying, especially for anything beyond a quick test.
- Prefer scoping the IAM policy down from `AmazonEC2FullAccess` once things are working.
- Consider using GitHub's OIDC integration with AWS IAM roles instead of long-lived access
  keys for better security (no secrets stored at all) — let me know if you'd like that version.
