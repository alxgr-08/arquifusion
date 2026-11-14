# infra/aws — owner: Rol 1 (Leonardo)

Terraform root for AWS. One file per component (`dynamodb.tf`, `iot.tf`, `firehose.tf`, `s3.tf`, `lambdas.tf`,
`stepfunctions.tf`, `cognito.tf`, `security.tf`, `location.tf`, `sns.tf`, `eventbridge.tf`).

## First run

```bash
# 1. Remote state bucket (once)
aws s3api create-bucket --bucket af-dev-tfstate-<ACCOUNT_ID> --region us-east-1
aws s3api put-bucket-versioning --bucket af-dev-tfstate-<ACCOUNT_ID> --versioning-configuration Status=Enabled

# 2. Local config (gitignored)
cp backend.hcl.example backend.hcl
cp terraform.tfvars.example terraform.tfvars   # fill ACCOUNT_ID

# 3. Init / plan / apply
terraform init -backend-config=backend.hcl
terraform plan
terraform apply
```

## AWS Academy rules
- Do not create `aws_iam_role`: pass `var.lab_role_arn` to Lambda, Step Functions, IoT Rules, Firehose, Scheduler.
- Credentials expire every ~4 h: refresh from "AWS Details" in the lab.

## Lambda packaging
Each `services/<name>/` is zipped together with `shared/af_shared` (use `archive_file` with a staging dir, or a
Lambda layer for `af_shared` + pydantic). Naming: `af-dev-<name-with-dashes>`.

## Outputs other roles need
| Output | Consumer |
|---|---|
| table name, event bus name | Rol 2, Rol 3 |
| IoT endpoint | Rol 2 (simulator) |
| API Gateway URL | Rol 3 (frontend) |
| Cognito user pool id / client id | Rol 3 (frontend) |
| Amazon Location map name + API key | Rol 3 (frontend), Rol 2 (route matrix) |
| SNS topic ARNs per role | Rol 2 (notify) |
