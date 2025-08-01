import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

import pandas_gbq
import pytest
from dagster import (
    AssetExecutionContext,
    AssetIn,
    AssetKey,
    EnvVar,
    IOManagerDefinition,
    MetadataValue,
    Out,
    TableColumn,
    TableSchema,
    TimeWindowPartitionMapping,
    asset,
    build_input_context,
    build_output_context,
    fs_io_manager,
    instance_for_test,
    job,
    materialize,
    op,
)
from dagster._core.definitions.partitions.definition import (
    DailyPartitionsDefinition,
    DynamicPartitionsDefinition,
    MultiPartitionsDefinition,
    StaticPartitionsDefinition,
)
from dagster._core.definitions.partitions.utils import MultiPartitionKey
from dagster._core.storage.db_io_manager import TableSlice
from dagster_gcp import build_bigquery_io_manager
from dagster_gcp_pyspark import (
    BigQueryPySparkIOManager,
    BigQueryPySparkTypeHandler,
    bigquery_pyspark_io_manager,
)
from google.cloud import bigquery
from pyspark.sql import DataFrame
from pyspark.sql.functions import col, to_date
from pyspark.sql.types import LongType, StringType, StructField, StructType

resource_config = {
    "database": "database_abc",
    "account": "account_abc",
    "user": "user_abc",
    "password": "password_abc",
    "warehouse": "warehouse_abc",
}

IS_BUILDKITE = os.getenv("BUILDKITE") is not None

SHARED_BUILDKITE_BQ_CONFIG = {
    "project": os.getenv("GCP_PROJECT_ID"),
    "temporary_gcs_bucket": "gcs_io_manager_test",
}

SCHEMA = "BIGQUERY_IO_MANAGER_SCHEMA"

pythonic_bigquery_io_manager = BigQueryPySparkIOManager(
    project=EnvVar("GCP_PROJECT_ID"), temporary_gcs_bucket="gcs_io_manager_test"
)
old_bigquery_io_manager = bigquery_pyspark_io_manager.configured(SHARED_BUILDKITE_BQ_CONFIG)


@contextmanager
def temporary_bigquery_table(schema_name: str) -> Iterator[str]:
    bq_client = bigquery.Client(
        project=SHARED_BUILDKITE_BQ_CONFIG["project"],
    )
    table_name = "test_io_manager_" + str(uuid.uuid4()).replace("-", "_")
    try:
        yield table_name
    finally:
        bq_client.query(
            f"drop table {SHARED_BUILDKITE_BQ_CONFIG['project']}.{schema_name}.{table_name}"
        ).result()


@pytest.mark.integration
def test_handle_output(spark):
    with patch("pyspark.sql.DataFrame.write") as mock_write:
        handler = BigQueryPySparkTypeHandler()

        columns = ["col1", "col2"]
        data = [("a", "b")]
        df = spark.createDataFrame(data).toDF(*columns)

        output_context = build_output_context(resource_config=resource_config)

        metadata = handler.handle_output(
            output_context,
            TableSlice(
                table="my_table",
                schema="my_schema",
                database="my_db",
                columns=None,
                partition_dimensions=None,
            ),
            df,
            None,
        )

        assert metadata == {
            "dataframe_columns": MetadataValue.table_schema(
                TableSchema(columns=[TableColumn("col1", "string"), TableColumn("col2", "string")])
            ),
        }

        assert len(mock_write.method_calls) == 1


@pytest.mark.integration
def test_load_input(spark):
    with patch("pyspark.sql.DataFrameReader.load") as mock_read:
        columns = ["col1", "col2"]
        data = [("a", "b")]
        df = spark.createDataFrame(data).toDF(*columns)
        mock_read.return_value = df

        handler = BigQueryPySparkTypeHandler()
        input_context = build_input_context(resource_config=resource_config)
        df = handler.load_input(
            input_context,
            TableSlice(
                table="my_table",
                schema="my_schema",
                database="my_db",
                columns=None,
                partition_dimensions=None,
            ),
            None,
        )
        assert mock_read.called


def test_build_bigquery_pyspark_io_manager():
    assert isinstance(
        build_bigquery_io_manager([BigQueryPySparkTypeHandler()]), IOManagerDefinition
    )
    # test wrapping decorator to make sure that works as expected
    assert isinstance(bigquery_pyspark_io_manager, IOManagerDefinition)


@pytest.mark.skipif(not IS_BUILDKITE, reason="Requires access to the BUILDKITE BigQuery DB")
@pytest.mark.parametrize("io_manager", [(old_bigquery_io_manager), (pythonic_bigquery_io_manager)])
@pytest.mark.integration
def test_io_manager_with_bigquery_pyspark(spark, io_manager):
    with temporary_bigquery_table(
        schema_name=SCHEMA,
    ) as table_name:
        # Create a job with the temporary table name as an output, so that it will write to that table
        # and not interfere with other runs of this test

        @op(
            out={table_name: Out(dagster_type=DataFrame, metadata={"schema": SCHEMA})},
        )
        def emit_pyspark_df(_):
            columns = ["foo", "quux"]
            data = [("bar", 1), ("baz", 2)]
            df = spark.createDataFrame(data).toDF(*columns)
            return df

        @op
        def read_pyspark_df(df: DataFrame) -> None:
            assert set([f.name for f in df.schema.fields]) == {"foo", "quux"}
            assert df.count() == 2

        @job(resource_defs={"io_manager": io_manager})
        def io_manager_test_job():
            read_pyspark_df(emit_pyspark_df())

        res = io_manager_test_job.execute_in_process()
        assert res.success


@pytest.mark.skipif(not IS_BUILDKITE, reason="Requires access to the BUILDKITE BigQuery DB")
@pytest.mark.parametrize("io_manager", [(old_bigquery_io_manager), (pythonic_bigquery_io_manager)])
@pytest.mark.integration
def test_time_window_partitioned_asset(spark, io_manager):
    with temporary_bigquery_table(
        schema_name=SCHEMA,
    ) as table_name:
        partitions_def = DailyPartitionsDefinition(start_date="2022-01-01")

        @asset(
            partitions_def=partitions_def,
            metadata={"partition_expr": "CAST(time as DATETIME)"},
            config_schema={"value": str},
            key_prefix=SCHEMA,
            name=table_name,
        )
        def daily_partitioned(context: AssetExecutionContext) -> DataFrame:
            partition = context.partition_key
            value = context.op_execution_context.op_config["value"]

            schema = StructType(
                [
                    StructField("RAW_TIME", StringType()),
                    StructField("A", StringType()),
                    StructField("B", LongType()),
                ]
            )
            data = [
                (partition, value, 4),
                (partition, value, 5),
                (partition, value, 6),
            ]
            df = spark.createDataFrame(data, schema=schema)
            df = df.withColumn("TIME", to_date(col("RAW_TIME")))

            return df

        @asset(
            partitions_def=partitions_def,
            key_prefix=SCHEMA,
            ins={"df": AssetIn([SCHEMA, table_name])},
            io_manager_key="fs_io",
        )
        def downstream_partitioned(df: DataFrame) -> None:
            # assert that we only get the columns created in daily_partitioned
            assert df.count() == 3

        asset_full_name = f"{SCHEMA}__{table_name}"
        bq_table_path = f"{SCHEMA}.{table_name}"

        resource_defs = {"io_manager": io_manager, "fs_io": fs_io_manager}

        materialize(
            [daily_partitioned, downstream_partitioned],
            partition_key="2022-01-01",
            resources=resource_defs,
            run_config={"ops": {asset_full_name: {"config": {"value": "1"}}}},
        )

        out_df = pandas_gbq.read_gbq(
            f"SELECT * FROM {bq_table_path}", project_id=SHARED_BUILDKITE_BQ_CONFIG["project"]
        )
        assert out_df["A"].tolist() == ["1", "1", "1"]

        materialize(
            [daily_partitioned, downstream_partitioned],
            partition_key="2022-01-02",
            resources=resource_defs,
            run_config={"ops": {asset_full_name: {"config": {"value": "2"}}}},
        )

        out_df = pandas_gbq.read_gbq(
            f"SELECT * FROM {bq_table_path}", project_id=SHARED_BUILDKITE_BQ_CONFIG["project"]
        )
        assert sorted(out_df["A"].tolist()) == ["1", "1", "1", "2", "2", "2"]

        materialize(
            [daily_partitioned, downstream_partitioned],
            partition_key="2022-01-01",
            resources=resource_defs,
            run_config={"ops": {asset_full_name: {"config": {"value": "3"}}}},
        )

        out_df = pandas_gbq.read_gbq(
            f"SELECT * FROM {bq_table_path}", project_id=SHARED_BUILDKITE_BQ_CONFIG["project"]
        )
        assert sorted(out_df["A"].tolist()) == ["2", "2", "2", "3", "3", "3"]


@pytest.mark.skipif(not IS_BUILDKITE, reason="Requires access to the BUILDKITE BigQuery DB")
@pytest.mark.parametrize("io_manager", [(old_bigquery_io_manager), (pythonic_bigquery_io_manager)])
@pytest.mark.integration
def test_static_partitioned_asset(spark, io_manager):
    with temporary_bigquery_table(
        schema_name=SCHEMA,
    ) as table_name:
        partitions_def = StaticPartitionsDefinition(["red", "yellow", "blue"])

        @asset(
            partitions_def=partitions_def,
            key_prefix=SCHEMA,
            metadata={"partition_expr": "color"},
            config_schema={"value": str},
            name=table_name,
        )
        def static_partitioned(context: AssetExecutionContext) -> DataFrame:
            partition = context.partition_key
            value = context.op_execution_context.op_config["value"]

            schema = StructType(
                [
                    StructField("COLOR", StringType()),
                    StructField("A", StringType()),
                    StructField("B", LongType()),
                ]
            )
            data = [(partition, value, 4), (partition, value, 5), (partition, value, 6)]
            df = spark.createDataFrame(data, schema=schema)
            return df

        @asset(
            partitions_def=partitions_def,
            key_prefix=SCHEMA,
            ins={"df": AssetIn([SCHEMA, table_name])},
            io_manager_key="fs_io",
        )
        def downstream_partitioned(df: DataFrame) -> None:
            # assert that we only get the columns created in static_partitioned
            assert df.count() == 3

        asset_full_name = f"{SCHEMA}__{table_name}"
        bq_table_path = f"{SCHEMA}.{table_name}"

        resource_defs = {"io_manager": io_manager, "fs_io": fs_io_manager}

        materialize(
            [static_partitioned, downstream_partitioned],
            partition_key="red",
            resources=resource_defs,
            run_config={"ops": {asset_full_name: {"config": {"value": "1"}}}},
        )

        out_df = pandas_gbq.read_gbq(
            f"SELECT * FROM {bq_table_path}", project_id=SHARED_BUILDKITE_BQ_CONFIG["project"]
        )
        assert out_df["A"].tolist() == ["1", "1", "1"]

        materialize(
            [static_partitioned, downstream_partitioned],
            partition_key="blue",
            resources=resource_defs,
            run_config={"ops": {asset_full_name: {"config": {"value": "2"}}}},
        )

        out_df = pandas_gbq.read_gbq(
            f"SELECT * FROM {bq_table_path}", project_id=SHARED_BUILDKITE_BQ_CONFIG["project"]
        )
        assert sorted(out_df["A"].tolist()) == ["1", "1", "1", "2", "2", "2"]

        materialize(
            [static_partitioned, downstream_partitioned],
            partition_key="red",
            resources=resource_defs,
            run_config={"ops": {asset_full_name: {"config": {"value": "3"}}}},
        )

        out_df = pandas_gbq.read_gbq(
            f"SELECT * FROM {bq_table_path}", project_id=SHARED_BUILDKITE_BQ_CONFIG["project"]
        )
        assert sorted(out_df["A"].tolist()) == ["2", "2", "2", "3", "3", "3"]


@pytest.mark.skipif(not IS_BUILDKITE, reason="Requires access to the BUILDKITE BigQuery DB")
@pytest.mark.parametrize("io_manager", [(old_bigquery_io_manager), (pythonic_bigquery_io_manager)])
@pytest.mark.integration
def test_multi_partitioned_asset(spark, io_manager):
    with temporary_bigquery_table(
        schema_name=SCHEMA,
    ) as table_name:
        partitions_def = MultiPartitionsDefinition(
            {
                "time": DailyPartitionsDefinition(start_date="2022-01-01"),
                "color": StaticPartitionsDefinition(["red", "yellow", "blue"]),
            }
        )

        @asset(
            partitions_def=partitions_def,
            key_prefix=SCHEMA,
            metadata={"partition_expr": {"time": "CAST(time as DATETIME)", "color": "color"}},
            config_schema={"value": str},
            name=table_name,
        )
        def multi_partitioned(context) -> DataFrame:
            partition = context.partition_key.keys_by_dimension
            value = context.op_execution_context.op_config["value"]

            schema = StructType(
                [
                    StructField("COLOR", StringType()),
                    StructField("RAW_TIME", StringType()),
                    StructField("A", StringType()),
                ]
            )
            data = [
                (partition["color"], partition["time"], value),
                (partition["color"], partition["time"], value),
                (partition["color"], partition["time"], value),
            ]
            df = spark.createDataFrame(data, schema=schema)
            df = df.withColumn("TIME", to_date(col("RAW_TIME")))

            return df

        @asset(
            partitions_def=partitions_def,
            key_prefix=SCHEMA,
            ins={"df": AssetIn([SCHEMA, table_name])},
            io_manager_key="fs_io",
        )
        def downstream_partitioned(df: DataFrame) -> None:
            # assert that we only get the columns created in multi_partitioned
            assert df.count() == 3

        asset_full_name = f"{SCHEMA}__{table_name}"
        bq_table_path = f"{SCHEMA}.{table_name}"

        resource_defs = {"io_manager": io_manager, "fs_io": fs_io_manager}

        materialize(
            [multi_partitioned, downstream_partitioned],
            partition_key=MultiPartitionKey({"time": "2022-01-01", "color": "red"}),
            resources=resource_defs,
            run_config={"ops": {asset_full_name: {"config": {"value": "1"}}}},
        )

        out_df = pandas_gbq.read_gbq(
            f"SELECT * FROM {bq_table_path}", project_id=SHARED_BUILDKITE_BQ_CONFIG["project"]
        )
        assert out_df["A"].tolist() == ["1", "1", "1"]

        materialize(
            [multi_partitioned, downstream_partitioned],
            partition_key=MultiPartitionKey({"time": "2022-01-01", "color": "blue"}),
            resources=resource_defs,
            run_config={"ops": {asset_full_name: {"config": {"value": "2"}}}},
        )

        out_df = pandas_gbq.read_gbq(
            f"SELECT * FROM {bq_table_path}", project_id=SHARED_BUILDKITE_BQ_CONFIG["project"]
        )
        assert sorted(out_df["A"].tolist()) == ["1", "1", "1", "2", "2", "2"]

        materialize(
            [multi_partitioned, downstream_partitioned],
            partition_key=MultiPartitionKey({"time": "2022-01-02", "color": "red"}),
            resources=resource_defs,
            run_config={"ops": {asset_full_name: {"config": {"value": "3"}}}},
        )

        out_df = pandas_gbq.read_gbq(
            f"SELECT * FROM {bq_table_path}", project_id=SHARED_BUILDKITE_BQ_CONFIG["project"]
        )
        assert sorted(out_df["A"].tolist()) == ["1", "1", "1", "2", "2", "2", "3", "3", "3"]

        materialize(
            [multi_partitioned, downstream_partitioned],
            partition_key=MultiPartitionKey({"time": "2022-01-01", "color": "red"}),
            resources=resource_defs,
            run_config={"ops": {asset_full_name: {"config": {"value": "4"}}}},
        )

        out_df = pandas_gbq.read_gbq(
            f"SELECT * FROM {bq_table_path}", project_id=SHARED_BUILDKITE_BQ_CONFIG["project"]
        )
        assert sorted(out_df["A"].tolist()) == ["2", "2", "2", "3", "3", "3", "4", "4", "4"]


@pytest.mark.skipif(not IS_BUILDKITE, reason="Requires access to the BUILDKITE BigQuery DB")
@pytest.mark.parametrize("io_manager", [(old_bigquery_io_manager), (pythonic_bigquery_io_manager)])
@pytest.mark.integration
def test_dynamic_partitions(spark, io_manager):
    with temporary_bigquery_table(
        schema_name=SCHEMA,
    ) as table_name:
        dynamic_fruits = DynamicPartitionsDefinition(name="dynamic_fruits")

        @asset(
            partitions_def=dynamic_fruits,
            key_prefix=SCHEMA,
            metadata={"partition_expr": "FRUIT"},
            config_schema={"value": str},
            name=table_name,
        )
        def dynamic_partitioned(context: AssetExecutionContext) -> DataFrame:
            partition = context.partition_key
            value = context.op_execution_context.op_config["value"]

            schema = StructType(
                [
                    StructField("FRUIT", StringType()),
                    StructField("A", StringType()),
                ]
            )
            data = [
                (partition, value),
                (partition, value),
                (partition, value),
            ]
            df = spark.createDataFrame(data, schema=schema)
            return df

        @asset(
            partitions_def=dynamic_fruits,
            key_prefix=SCHEMA,
            ins={"df": AssetIn([SCHEMA, table_name])},
            io_manager_key="fs_io",
        )
        def downstream_partitioned(df: DataFrame) -> None:
            # assert that we only get the columns created in dynamic_partitioned
            assert df.count() == 3

        asset_full_name = f"{SCHEMA}__{table_name}"
        bq_table_path = f"{SCHEMA}.{table_name}"

        resource_defs = {"io_manager": io_manager, "fs_io": fs_io_manager}

        with instance_for_test() as instance:
            instance.add_dynamic_partitions(dynamic_fruits.name, ["apple"])  # pyright: ignore[reportArgumentType]

            materialize(
                [dynamic_partitioned, downstream_partitioned],
                partition_key="apple",
                resources=resource_defs,
                instance=instance,
                run_config={"ops": {asset_full_name: {"config": {"value": "1"}}}},
            )

            out_df = pandas_gbq.read_gbq(
                f"SELECT * FROM {bq_table_path}", project_id=SHARED_BUILDKITE_BQ_CONFIG["project"]
            )
            assert out_df["A"].tolist() == ["1", "1", "1"]

            instance.add_dynamic_partitions(dynamic_fruits.name, ["orange"])  # pyright: ignore[reportArgumentType]

            materialize(
                [dynamic_partitioned, downstream_partitioned],
                partition_key="orange",
                resources=resource_defs,
                instance=instance,
                run_config={"ops": {asset_full_name: {"config": {"value": "2"}}}},
            )

            out_df = pandas_gbq.read_gbq(
                f"SELECT * FROM {bq_table_path}", project_id=SHARED_BUILDKITE_BQ_CONFIG["project"]
            )
            assert sorted(out_df["A"].tolist()) == ["1", "1", "1", "2", "2", "2"]

            materialize(
                [dynamic_partitioned, downstream_partitioned],
                partition_key="apple",
                resources=resource_defs,
                instance=instance,
                run_config={"ops": {asset_full_name: {"config": {"value": "3"}}}},
            )

            out_df = pandas_gbq.read_gbq(
                f"SELECT * FROM {bq_table_path}", project_id=SHARED_BUILDKITE_BQ_CONFIG["project"]
            )
            assert sorted(out_df["A"].tolist()) == ["2", "2", "2", "3", "3", "3"]


@pytest.mark.skipif(not IS_BUILDKITE, reason="Requires access to the BUILDKITE bigquery DB")
@pytest.mark.parametrize("io_manager", [(old_bigquery_io_manager), (pythonic_bigquery_io_manager)])
@pytest.mark.integration
def test_self_dependent_asset(spark, io_manager):
    with temporary_bigquery_table(
        schema_name=SCHEMA,
    ) as table_name:
        daily_partitions = DailyPartitionsDefinition(start_date="2023-01-01")

        @asset(
            partitions_def=daily_partitions,
            key_prefix=SCHEMA,
            ins={
                "self_dependent_asset": AssetIn(
                    key=AssetKey([SCHEMA, table_name]),
                    partition_mapping=TimeWindowPartitionMapping(start_offset=-1, end_offset=-1),
                ),
            },
            metadata={
                "partition_expr": "TIMESTAMP(key)",
            },
            config_schema={"value": str, "last_partition_key": str},
            name=table_name,
        )
        def self_dependent_asset(
            context: AssetExecutionContext, self_dependent_asset: DataFrame
        ) -> DataFrame:
            key = context.partition_key

            if not self_dependent_asset.isEmpty():
                pd_df = self_dependent_asset.toPandas()
                assert len(pd_df.index) == 3
                assert (pd_df["key"] == context.op_config["last_partition_key"]).all()
            else:
                assert context.op_execution_context.op_config["last_partition_key"] == "NA"
            value = context.op_execution_context.op_config["value"]
            schema = StructType(
                [
                    StructField("KEY", StringType()),
                    StructField("A", StringType()),
                ]
            )
            data = [
                (key, value),
                (key, value),
                (key, value),
            ]
            df = spark.createDataFrame(data, schema=schema)

            return df

        asset_full_name = f"{SCHEMA}__{table_name}"
        bq_table_path = f"{SCHEMA}.{table_name}"

        resource_defs = {"io_manager": io_manager}

        materialize(
            [self_dependent_asset],
            partition_key="2023-01-01",
            resources=resource_defs,
            run_config={
                "ops": {asset_full_name: {"config": {"value": "1", "last_partition_key": "NA"}}}
            },
        )

        out_df = pandas_gbq.read_gbq(
            f"SELECT * FROM {bq_table_path}", project_id=SHARED_BUILDKITE_BQ_CONFIG["project"]
        )
        assert sorted(out_df["A"].tolist()) == ["1", "1", "1"]

        materialize(
            [self_dependent_asset],
            partition_key="2023-01-02",
            resources=resource_defs,
            run_config={
                "ops": {
                    asset_full_name: {"config": {"value": "2", "last_partition_key": "2023-01-01"}}
                }
            },
        )

        out_df = pandas_gbq.read_gbq(
            f"SELECT * FROM {bq_table_path}", project_id=SHARED_BUILDKITE_BQ_CONFIG["project"]
        )
        assert sorted(out_df["A"].tolist()) == ["1", "1", "1", "2", "2", "2"]
