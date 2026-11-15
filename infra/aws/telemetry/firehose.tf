resource "aws_kinesis_firehose_delivery_stream" "telemetry" {
  # The AWS provider uses DirectPut when no source stream is configured.
  name        = "${local.prefix}-telemetry"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn            = var.lab_role_arn
    bucket_arn          = data.aws_s3_bucket.lake.arn
    prefix              = local.data_prefix
    error_output_prefix = "${local.error_prefix}!{firehose:error-output-type}/"
    buffering_size      = 64
    buffering_interval  = 60
    # The Parquet writer compresses columns. S3 must receive the file without a second wrapper.
    compression_format = "UNCOMPRESSED"

    data_format_conversion_configuration {
      enabled = true
      input_format_configuration {
        deserializer {
          open_x_json_ser_de {
            case_insensitive = true
          }
        }
      }
      output_format_configuration {
        serializer {
          parquet_ser_de {
            compression = "SNAPPY"
          }
        }
      }
      schema_configuration {
        role_arn      = var.lab_role_arn
        catalog_id    = var.account_id
        region        = var.region
        database_name = aws_glue_catalog_table.telemetry.database_name
        table_name    = aws_glue_catalog_table.telemetry.name
        version_id    = "LATEST"
      }
    }
  }

  depends_on = [
    aws_s3_bucket_public_access_block.lake,
    aws_s3_bucket_server_side_encryption_configuration.lake,
    aws_s3_bucket_versioning.lake,
  ]
}

output "settings" {
  value = {
    AF_FIREHOSE_STREAM = aws_kinesis_firehose_delivery_stream.telemetry.name
    AF_LAKE_BUCKET     = data.aws_s3_bucket.lake.id
    AF_LAKE_ACCOUNT_ID = var.account_id
    AF_PARQUET_PREFIX  = local.data_prefix
    error_prefix       = local.error_prefix
    glue_database      = aws_glue_catalog_table.telemetry.database_name
    glue_table         = aws_glue_catalog_table.telemetry.name
  }
}
