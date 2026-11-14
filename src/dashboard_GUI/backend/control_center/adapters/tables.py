"""Streaming typed artifact readers, retaining physical row order and exact strings."""

import csv
import io
import math


def columns(kind):
    from simulation_env.dataset import DECISION_COLUMNS, OUTCOME_COLUMNS

    return dict(DECISION_COLUMNS if kind == "decisions" else OUTCOME_COLUMNS)


def rows(handle, filename):
    kind, fmt = filename.split(".")
    kinds = columns(kind)
    if fmt == "parquet":
        import pyarrow.parquet as pq

        parquet = pq.ParquetFile(handle)
        import pyarrow as pa

        expected = {
            "string": pa.string(),
            "int32": pa.int32(),
            "float64": pa.float64(),
            "bool_": pa.bool_(),
        }
        if set(parquet.schema_arrow.names) != set(kinds):
            raise ValueError("Unsupported or incomplete table schema")
        if any(
            parquet.schema_arrow.field(k).type != expected[t] for k, t in kinds.items()
        ):
            raise ValueError("Table column types do not match production schema")
        for batch in parquet.iter_batches(batch_size=512):
            for row in batch.to_pylist():
                yield finite(row)
    else:
        text = io.TextIOWrapper(handle, encoding="utf-8", newline="")
        try:
            reader = csv.DictReader(text)
            fields = reader.fieldnames or []
            if len(fields) != len(set(fields)) or set(fields) != set(kinds):
                raise ValueError("Table lacks unique identity columns")
            for row in reader:
                if None in row or None in row.values():
                    raise ValueError("Malformed CSV row")
                result = {}
                for key, value in row.items():
                    typ = kinds.get(key, "string")
                    if typ == "string":
                        result[key] = value
                    elif value == "":
                        result[key] = None
                    elif typ == "bool_":
                        if value.lower() not in ("true", "false", "1", "0"):
                            raise ValueError("Malformed boolean")
                        result[key] = value.lower() in ("true", "1")
                    else:
                        result[key] = int(value) if typ == "int32" else float(value)
                yield finite(result)
        finally:
            text.detach()


def finite(row):
    if any(isinstance(v, float) and not math.isfinite(v) for v in row.values()):
        raise ValueError("Nonfinite table value")
    return row
