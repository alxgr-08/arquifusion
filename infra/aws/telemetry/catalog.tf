locals {
  # Top-level Hive names are lowercase in the Firehose Parquet. The struct fields
  # retain the spelling of contracts/telemetry.schema.json, including nullable metrics.
  columns = [
    { name = "schemaversion", type = "string" },
    { name = "messageid", type = "string" },
    { name = "machineid", type = "string" },
    { name = "operationid", type = "string" },
    { name = "model", type = "string" },
    { name = "machinetype", type = "string" },
    { name = "timestamp", type = "timestamp" },
    { name = "location", type = "struct<lat:double,lon:double,zoneId:string>" },
    { name = "status", type = "string" },
    { name = "metrics", type = "struct<engineHours:double,temperatureC:double,vibrationMmS:double,pressureBar:double,fuelRateLph:double,fuelLevelPct:double,loadTons:double,speedKmh:double,rpm:double>" },
    { name = "context", type = "struct<fuelBatchId:string,ambientTempC:double>" }
  ]
}

resource "aws_glue_catalog_database" "telemetry" {
  catalog_id  = var.account_id
  name        = "af_${var.env}_telemetry"
  description = "ArquiFusion telemetry in the private regional data lake"

  depends_on = [data.aws_s3_bucket.lake]
}

resource "aws_glue_catalog_table" "telemetry" {
  catalog_id    = var.account_id
  database_name = aws_glue_catalog_database.telemetry.name
  name          = "telemetry"
  table_type    = "EXTERNAL_TABLE"
  parameters    = { classification = "parquet" }

  storage_descriptor {
    location      = "s3://${data.aws_s3_bucket.lake.id}/${local.data_prefix}"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    dynamic "columns" {
      for_each = local.columns
      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }
}
