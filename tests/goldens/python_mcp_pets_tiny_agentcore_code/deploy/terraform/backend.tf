# State lives in the S3 bucket that bootstrap/ creates. The deploy script and the
# GitHub workflow pass the settings with -backend-config (see backend.hcl.example).
terraform {
  backend "s3" {}
}
