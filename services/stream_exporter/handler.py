"""Export a bounded Streams invocation in per-schema batches. Lambda retries from the first failed sequence."""

import logging
import os
import re
from collections import defaultdict

from services.stream_exporter.lake_io import CHUNK, configured_archive
from services.stream_exporter.projection import project

LOG = logging.getLogger(__name__)


def export(records, archive, expected_arn):
    groups = defaultdict(list)
    failures = []
    for record in records:
        sequence = record["dynamodb"]["SequenceNumber"]
        try:
            for kind, item in project(record, expected_arn):
                groups[kind].append((sequence, item))
        except (KeyError, TypeError, ValueError) as exc:
            LOG.error("Projection failed at sequence %s (%s)", sequence, type(exc).__name__)
            failures.append({"itemIdentifier": sequence})
            break
    for kind, group in groups.items():
        for start in range(0, len(group), CHUNK):
            chunk = group[start : start + CHUNK]
            try:
                archive.publish(kind, [item for _, item in chunk])
            except Exception as exc:
                LOG.error("Archive failed for %s at sequence %s (%s)", kind, chunk[0][0], type(exc).__name__)
                failures.append({"itemIdentifier": chunk[0][0]})
                break
    return {"batchItemFailures": failures}


def handler(event, context):
    archive = configured_archive()
    arn = os.environ["AF_SOURCE_STREAM_ARN"]
    if not re.fullmatch(rf"arn:aws:dynamodb:us-east-1:{archive.owner}:table/af-dev-arquifusion/stream/[^/]+", arn):
        raise ValueError("configure the ArquiFusion Academy stream ARN")
    return export(event["Records"], archive, arn)
