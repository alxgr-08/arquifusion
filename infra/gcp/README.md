# infra/gcp — owner: Rol 1 (Leonardo)

Terraform root for Google Cloud.

## To build
- [ ] BigQuery datasets `af_lake`, `af_ml`, `af_out` (location US)
- [ ] Table `af_lake.telemetry` partitioned by DATE(timestamp), clustered by machineId (schema = contracts/telemetry)
- [ ] Service account `af-dev-aws-bridge` with BigQuery Data Editor + Job User
- [ ] Store its JSON key in AWS Secrets Manager as `af/gcp/service-account` (never in the repo)
- [ ] Scheduled queries from `analytics/` (Rol 3 provides the SQL)

## First run

```bash
gcloud auth application-default login
cp terraform.tfvars.example terraform.tfvars
terraform init -backend-config=../aws/backend.hcl -backend-config="key=gcp/terraform.tfstate"
terraform plan
```
