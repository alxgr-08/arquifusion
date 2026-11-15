output "table_name" {
  value = aws_dynamodb_table.arquifusion.name
}

output "table_arn" {
  value = aws_dynamodb_table.arquifusion.arn
}

output "table_stream_arn" {
  value = aws_dynamodb_table.arquifusion.stream_arn
}
