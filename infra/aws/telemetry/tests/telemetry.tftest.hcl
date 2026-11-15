mock_provider "aws" {}

variables {
  account_id   = "123456789012"
  env          = "dev"
  region       = "us-east-1"
  lab_role_arn = "arn:aws:iam::123456789012:role/LabRole"
}

override_data {
  target = data.aws_s3_bucket.lake
  values = {
    id            = "af-dev-arquifusion-lake-123456789012-us-east-1-an"
    arn           = "arn:aws:s3:::af-dev-arquifusion-lake-123456789012-us-east-1-an"
    bucket_region = "us-east-1"
  }
}

override_data {
  target = data.aws_resourcegroupstaggingapi_resources.lake
  values = {
    resource_tag_mapping_list = [{
      resource_arn = "arn:aws:s3:::af-dev-arquifusion-lake-123456789012-us-east-1-an"
      tags         = { project = "arquifusion", env = "dev", owner = "rol1", purpose = "telemetry-lake" }
    }]
  }
}

run "contract_and_destination" {
  command = plan

  # Compare every physical column and every struct field/type with the JSON contract.
  # The order of fields inside a Hive struct is irrelevant to this name-based comparison.
  assert {
    condition = {
      for column in aws_glue_catalog_table.telemetry.storage_descriptor[0].columns : column.name => (
        startswith(column.type, "struct<") ?
        join(",", sort(split(",", trimsuffix(trimprefix(column.type, "struct<"), ">")))) : column.type
      )
      } == {
      for name, field in jsondecode(file("${path.module}/../../../contracts/telemetry.schema.json")).properties : lower(name) => try(
        join(",", sort([
          for child_name, child in field.properties :
          format("%s:%s", child_name, contains(flatten([child.type]), "number") ? "double" : "string")
        ])),
        name == "timestamp" ? "timestamp" : "string"
      )
    }
    error_message = "The Glue table must cover the whole telemetry contract with the tested Parquet types."
  }

  assert {
    condition = alltrue([
      output.settings.AF_FIREHOSE_STREAM == "af-dev-telemetry",
      output.settings.AF_LAKE_BUCKET == "af-dev-arquifusion-lake-123456789012-us-east-1-an",
      aws_glue_catalog_table.telemetry.storage_descriptor[0].location == "s3://${output.settings.AF_LAKE_BUCKET}/${output.settings.AF_PARQUET_PREFIX}",
      aws_kinesis_firehose_delivery_stream.telemetry.extended_s3_configuration[0].prefix == output.settings.AF_PARQUET_PREFIX,
      aws_kinesis_firehose_delivery_stream.telemetry.extended_s3_configuration[0].error_output_prefix == "errors/!{firehose:error-output-type}/",
      aws_kinesis_firehose_delivery_stream.telemetry.extended_s3_configuration[0].data_format_conversion_configuration[0].output_format_configuration[0].serializer[0].parquet_ser_de[0].compression == "SNAPPY",
      aws_s3_bucket_public_access_block.lake.block_public_acls,
      aws_s3_bucket_public_access_block.lake.ignore_public_acls,
      aws_s3_bucket_public_access_block.lake.block_public_policy,
      aws_s3_bucket_public_access_block.lake.restrict_public_buckets,
      aws_s3_bucket_versioning.lake.versioning_configuration[0].status == "Enabled",
      one(aws_s3_bucket_server_side_encryption_configuration.lake.rule).apply_server_side_encryption_by_default[0].sse_algorithm == "AES256",
    ])
    error_message = "Verify the producer, consumer, private bucket and the Firehose Parquet destination together."
  }
}

run "reject_bucket_with_wrong_purpose" {
  command = plan
  override_data {
    target = data.aws_resourcegroupstaggingapi_resources.lake
    values = {
      resource_tag_mapping_list = [{
        resource_arn = "arn:aws:s3:::af-dev-arquifusion-lake-123456789012-us-east-1-an"
        tags         = { project = "arquifusion", env = "dev", owner = "rol1", purpose = "tfstate" }
      }]
    }
  }
  expect_failures = [data.aws_s3_bucket.lake]
}

run "reject_missing_bucket" {
  command = plan
  override_data {
    target = data.aws_resourcegroupstaggingapi_resources.lake
    values = { resource_tag_mapping_list = [] }
  }
  expect_failures = [data.aws_s3_bucket.lake]
}
