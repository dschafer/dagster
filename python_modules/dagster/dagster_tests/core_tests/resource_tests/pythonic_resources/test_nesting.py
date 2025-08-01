import contextlib
import enum
import json
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

import dagster as dg
import pytest
from dagster import Definitions
from dagster._config.pythonic_config import ConfigurableResourceFactory
from dagster._core.execution.context.init import InitResourceContext


def test_nested_resources() -> None:
    out_txt = []

    class Writer(dg.ConfigurableResource, ABC):
        @abstractmethod
        def output(self, text: str) -> None:
            pass

    class WriterResource(Writer):
        def output(self, text: str) -> None:
            out_txt.append(text)

    class PrefixedWriterResource(Writer):
        prefix: str

        def output(self, text: str) -> None:
            out_txt.append(f"{self.prefix}{text}")

    class JsonWriterResource(
        Writer,
    ):
        base_writer: Writer
        indent: int

        def output(self, obj: Any) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
            self.base_writer.output(json.dumps(obj, indent=self.indent))

    @dg.asset
    def hello_world_asset(writer: JsonWriterResource):
        writer.output({"hello": "world"})

    # Construct a resource that is needed by another resource
    writer_resource = WriterResource()
    json_writer_resource = JsonWriterResource(indent=2, base_writer=writer_resource)

    assert (
        dg.Definitions(
            assets=[hello_world_asset],
            resources={
                "writer": json_writer_resource,
            },
        )
        .resolve_implicit_global_asset_job_def()
        .execute_in_process()
        .success
    )

    assert out_txt == ['{\n  "hello": "world"\n}']

    # Do it again, with a different nested resource
    out_txt.clear()
    prefixed_writer_resource = PrefixedWriterResource(prefix="greeting: ")
    prefixed_json_writer_resource = JsonWriterResource(
        indent=2, base_writer=prefixed_writer_resource
    )

    assert (
        dg.Definitions(
            assets=[hello_world_asset],
            resources={
                "writer": prefixed_json_writer_resource,
            },
        )
        .resolve_implicit_global_asset_job_def()
        .execute_in_process()
        .success
    )

    assert out_txt == ['greeting: {\n  "hello": "world"\n}']


def test_nested_resources_multiuse() -> None:
    class AWSCredentialsResource(dg.ConfigurableResource):
        username: str
        password: str

    class S3Resource(dg.ConfigurableResource):
        aws_credentials: AWSCredentialsResource
        bucket_name: str

    class EC2Resource(dg.ConfigurableResource):
        aws_credentials: AWSCredentialsResource

    completed = {}

    @dg.asset
    def my_asset(s3: S3Resource, ec2: EC2Resource):
        assert s3.aws_credentials.username == "foo"
        assert s3.aws_credentials.password == "bar"
        assert s3.bucket_name == "my_bucket"

        assert ec2.aws_credentials.username == "foo"
        assert ec2.aws_credentials.password == "bar"

        completed["yes"] = True

    aws_credentials = AWSCredentialsResource(username="foo", password="bar")
    defs = dg.Definitions(
        assets=[my_asset],
        resources={
            "s3": S3Resource(bucket_name="my_bucket", aws_credentials=aws_credentials),
            "ec2": EC2Resource(aws_credentials=aws_credentials),
        },
    )

    assert defs.resolve_implicit_global_asset_job_def().execute_in_process().success
    assert completed["yes"]


def test_nested_resources_runtime_config() -> None:
    class AWSCredentialsResource(dg.ConfigurableResource):
        username: str
        password: str

    class S3Resource(dg.ConfigurableResource):
        aws_credentials: AWSCredentialsResource
        bucket_name: str

    class EC2Resource(dg.ConfigurableResource):
        aws_credentials: AWSCredentialsResource

    completed = {}

    @dg.asset
    def my_asset(s3: S3Resource, ec2: EC2Resource):
        assert s3.aws_credentials.username == "foo"
        assert s3.aws_credentials.password == "bar"
        assert s3.bucket_name == "my_bucket"

        assert ec2.aws_credentials.username == "foo"
        assert ec2.aws_credentials.password == "bar"

        completed["yes"] = True

    aws_credentials = AWSCredentialsResource.configure_at_launch()
    s3_resource = S3Resource(bucket_name="my_bucket", aws_credentials=aws_credentials)
    ec2_resource = EC2Resource(aws_credentials=aws_credentials)

    defs = dg.Definitions(
        assets=[my_asset],
        resources={
            "aws_credentials": aws_credentials,
            "s3": s3_resource,
            "ec2": ec2_resource,
        },
    )

    assert (
        defs.resolve_implicit_global_asset_job_def()
        .execute_in_process(
            {
                "resources": {
                    "aws_credentials": {
                        "config": {
                            "username": "foo",
                            "password": "bar",
                        }
                    }
                }
            }
        )
        .success
    )
    assert completed["yes"]

    @dg.job(
        resource_defs={
            "random_key": aws_credentials,
            "s3": s3_resource,
            "ec2": ec2_resource,
        }
    )
    def my_job():
        my_asset()

    assert my_job.execute_in_process(
        {
            "resources": {
                "random_key": {
                    "config": {
                        "username": "foo",
                        "password": "bar",
                    }
                }
            }
        }
    ).success


def test_nested_resources_runtime_config_complex() -> None:
    class CredentialsResource(dg.ConfigurableResource):
        username: str
        password: str

    class DBConfigResource(dg.ConfigurableResource):
        creds: CredentialsResource
        host: str
        database: str

    class DBResource(dg.ConfigurableResource):
        config: DBConfigResource

    completed = {}

    @dg.asset
    def my_asset(db: DBResource):
        assert db.config.creds.username == "foo"
        assert db.config.creds.password == "bar"
        assert db.config.host == "localhost"
        assert db.config.database == "my_db"
        completed["yes"] = True

    credentials = CredentialsResource.configure_at_launch()
    db_config = DBConfigResource.configure_at_launch(creds=credentials)
    db = DBResource(config=db_config)

    defs = dg.Definitions(
        assets=[my_asset],
        resources={
            "credentials": credentials,
            "db_config": db_config,
            "db": db,
        },
    )

    assert (
        defs.resolve_implicit_global_asset_job_def()
        .execute_in_process(
            {
                "resources": {
                    "credentials": {
                        "config": {
                            "username": "foo",
                            "password": "bar",
                        }
                    },
                    "db_config": {
                        "config": {
                            "host": "localhost",
                            "database": "my_db",
                        }
                    },
                }
            }
        )
        .success
    )
    assert completed["yes"]

    credentials = CredentialsResource.configure_at_launch()
    db_config = DBConfigResource(creds=credentials, host="localhost", database="my_db")
    db = DBResource(config=db_config)

    defs = dg.Definitions(
        assets=[my_asset],
        resources={
            "credentials": credentials,
            "db": db,
        },
    )

    assert (
        defs.resolve_implicit_global_asset_job_def()
        .execute_in_process(
            {
                "resources": {
                    "credentials": {
                        "config": {
                            "username": "foo",
                            "password": "bar",
                        }
                    },
                }
            }
        )
        .success
    )
    assert completed["yes"]


def test_nested_function_resource() -> None:
    out_txt = []

    @dg.resource
    def writer_resource(context):
        def output(text: str) -> None:
            out_txt.append(text)

        return output

    class PostfixWriterResource(ConfigurableResourceFactory[Callable[[str], None]]):
        writer: dg.ResourceDependency[Callable[[str], None]]
        postfix: str

        def create_resource(self, context) -> Callable[[str], None]:
            def output(text: str):
                self.writer(f"{text}{self.postfix}")

            return output

    @dg.asset
    def my_asset(writer: dg.ResourceParam[Callable[[str], None]]):
        writer("foo")
        writer("bar")

    defs = dg.Definitions(
        assets=[my_asset],
        resources={
            "writer": PostfixWriterResource(writer=writer_resource, postfix="!"),
        },
    )

    assert defs.resolve_implicit_global_asset_job_def().execute_in_process().success
    assert out_txt == ["foo!", "bar!"]


def test_nested_function_resource_configured() -> None:
    out_txt = []

    @dg.resource(config_schema={"prefix": dg.Field(str, default_value="")})
    def writer_resource(context):
        prefix = context.resource_config["prefix"]

        def output(text: str) -> None:
            out_txt.append(f"{prefix}{text}")

        return output

    class PostfixWriterResource(ConfigurableResourceFactory[Callable[[str], None]]):
        writer: dg.ResourceDependency[Callable[[str], None]]
        postfix: str

        def create_resource(self, context) -> Callable[[str], None]:
            def output(text: str):
                self.writer(f"{text}{self.postfix}")

            return output

    @dg.asset
    def my_asset(writer: dg.ResourceParam[Callable[[str], None]]):
        writer("foo")
        writer("bar")

    defs = dg.Definitions(
        assets=[my_asset],
        resources={
            "writer": PostfixWriterResource(writer=writer_resource, postfix="!"),
        },
    )

    assert defs.resolve_implicit_global_asset_job_def().execute_in_process().success
    assert out_txt == ["foo!", "bar!"]

    out_txt.clear()

    defs = dg.Definitions(
        assets=[my_asset],
        resources={
            "writer": PostfixWriterResource(
                writer=writer_resource.configured({"prefix": "msg: "}), postfix="!"
            ),
        },
    )

    assert defs.resolve_implicit_global_asset_job_def().execute_in_process().success
    assert out_txt == ["msg: foo!", "msg: bar!"]


def test_nested_function_resource_runtime_config() -> None:
    out_txt = []

    @dg.resource(config_schema={"prefix": str})
    def writer_resource(context):
        prefix = context.resource_config["prefix"]

        def output(text: str) -> None:
            out_txt.append(f"{prefix}{text}")

        return output

    class PostfixWriterResource(ConfigurableResourceFactory[Callable[[str], None]]):
        writer: dg.ResourceDependency[Callable[[str], None]]
        postfix: str

        def create_resource(self, context) -> Callable[[str], None]:
            def output(text: str):
                self.writer(f"{text}{self.postfix}")

            return output

    @dg.asset
    def my_asset(writer: dg.ResourceParam[Callable[[str], None]]):
        writer("foo")
        writer("bar")

    with pytest.raises(
        dg.DagsterInvalidDefinitionError,
        match="Any partially configured, nested resources must be provided as a top level resource.",
    ):
        # errors b/c writer_resource is not configured
        # and not provided as a top-level resource to Definitions
        Definitions.validate_loadable(
            dg.Definitions(
                assets=[my_asset],
                resources={
                    "writer": PostfixWriterResource(writer=writer_resource, postfix="!"),
                },
            )
        )

    defs = dg.Definitions(
        assets=[my_asset],
        resources={
            "base_writer": writer_resource,
            "writer": PostfixWriterResource(writer=writer_resource, postfix="!"),
        },
    )

    assert (
        defs.resolve_implicit_global_asset_job_def()
        .execute_in_process(
            {
                "resources": {
                    "base_writer": {
                        "config": {
                            "prefix": "msg: ",
                        },
                    },
                },
            }
        )
        .success
    )
    assert out_txt == ["msg: foo!", "msg: bar!"]


def test_nested_resource_raw_value() -> None:
    class MyResourceWithDep(dg.ConfigurableResource):
        a_string: dg.ResourceDependency[str]

    @dg.resource
    def string_resource(context) -> str:
        return "foo"

    executed = {}

    @dg.asset
    def my_asset(my_resource: MyResourceWithDep):
        assert my_resource.a_string == "foo"
        executed["yes"] = True

    defs = dg.Definitions(
        assets=[my_asset],
        resources={
            "my_resource": MyResourceWithDep(a_string=string_resource),
        },
    )
    assert defs.resolve_implicit_global_asset_job_def().execute_in_process().success
    assert executed["yes"]

    executed.clear()

    defs = dg.Definitions(
        assets=[my_asset],
        resources={"my_resource": MyResourceWithDep(a_string="foo")},
    )
    assert defs.resolve_implicit_global_asset_job_def().execute_in_process().success
    assert executed["yes"]


def test_nested_resource_raw_value_io_manager() -> None:
    class MyMultiwriteIOManager(dg.ConfigurableIOManager):
        base_io_manager: dg.ResourceDependency[dg.IOManager]
        mirror_io_manager: dg.ResourceDependency[dg.IOManager]

        def handle_output(self, context, obj) -> None:
            self.base_io_manager.handle_output(context, obj)
            self.mirror_io_manager.handle_output(context, obj)

        def load_input(self, context) -> Any:
            return self.base_io_manager.load_input(context)

    log = []

    class ConfigIOManager(dg.ConfigurableIOManager):
        path_prefix: list[str]

        def handle_output(self, context, obj) -> None:
            log.append(
                "ConfigIOManager handle_output "
                + "/".join(self.path_prefix + list(context.asset_key.path))
            )

        def load_input(self, context) -> Any:
            log.append(
                "ConfigIOManager load_input "
                + "/".join(self.path_prefix + list(context.asset_key.path))
            )
            return "foo"

    class RawIOManager(dg.IOManager):
        def handle_output(self, context, obj) -> None:
            log.append("RawIOManager handle_output " + "/".join(list(context.asset_key.path)))

        def load_input(self, context) -> Any:
            log.append("RawIOManager load_input " + "/".join(list(context.asset_key.path)))
            return "foo"

    @dg.asset
    def my_asset() -> str:
        return "foo"

    @dg.asset
    def my_downstream_asset(my_asset: str) -> str:
        return my_asset + "bar"

    defs = dg.Definitions(
        assets=[my_asset, my_downstream_asset],
        resources={
            "io_manager": MyMultiwriteIOManager(
                base_io_manager=ConfigIOManager(path_prefix=["base"]),
                mirror_io_manager=RawIOManager(),
            ),
        },
    )

    assert defs.resolve_implicit_global_asset_job_def().execute_in_process().success
    assert log == [
        "ConfigIOManager handle_output base/my_asset",
        "RawIOManager handle_output my_asset",
        "ConfigIOManager load_input base/my_asset",
        "ConfigIOManager handle_output base/my_downstream_asset",
        "RawIOManager handle_output my_downstream_asset",
    ]


def test_enum_nested_resource_no_run_config() -> None:
    class MyEnum(enum.Enum):
        A = "a_value"
        B = "b_value"

    class ResourceWithEnum(dg.ConfigurableResource):
        my_enum: MyEnum

    class OuterResourceWithResourceWithEnum(dg.ConfigurableResource):
        resource_with_enum: ResourceWithEnum

    @dg.asset
    def asset_with_outer_resource(outer_resource: OuterResourceWithResourceWithEnum):
        return outer_resource.resource_with_enum.my_enum.value

    defs = dg.Definitions(
        assets=[asset_with_outer_resource],
        resources={
            "outer_resource": OuterResourceWithResourceWithEnum(
                resource_with_enum=ResourceWithEnum(my_enum=MyEnum.A)
            )
        },
    )

    a_job = defs.resolve_implicit_global_asset_job_def()

    result = a_job.execute_in_process()
    assert result.success
    assert result.output_for_node("asset_with_outer_resource") == "a_value"


def test_enum_nested_resource_run_config_override() -> None:
    class MyEnum(enum.Enum):
        A = "a_value"
        B = "b_value"

    class ResourceWithEnum(dg.ConfigurableResource):
        my_enum: MyEnum

    class OuterResourceWithResourceWithEnum(dg.ConfigurableResource):
        resource_with_enum: ResourceWithEnum

    @dg.asset
    def asset_with_outer_resource(outer_resource: OuterResourceWithResourceWithEnum):
        return outer_resource.resource_with_enum.my_enum.value

    resource_with_enum = ResourceWithEnum.configure_at_launch()
    defs = dg.Definitions(
        assets=[asset_with_outer_resource],
        resources={
            "resource_with_enum": resource_with_enum,
            "outer_resource": OuterResourceWithResourceWithEnum(
                resource_with_enum=resource_with_enum
            ),
        },
    )

    a_job = defs.resolve_implicit_global_asset_job_def()

    # Case: I'm re-specifying the nested enum at runtime - expect the runtime config to override the resource config
    result = a_job.execute_in_process(
        run_config={"resources": {"resource_with_enum": {"config": {"my_enum": "B"}}}}
    )
    assert result.success
    assert result.output_for_node("asset_with_outer_resource") == "b_value"


def test_nested_resource_raw_value_io_manager_with_setup_teardown() -> None:
    log = []

    class MyMultiwriteIOManager(dg.ConfigurableIOManager):
        base_io_manager: dg.ResourceDependency[dg.IOManager]
        mirror_io_manager: dg.ResourceDependency[dg.IOManager]

        def handle_output(self, context, obj) -> None:
            self.base_io_manager.handle_output(context, obj)
            self.mirror_io_manager.handle_output(context, obj)

        def load_input(self, context) -> Any:
            return self.base_io_manager.load_input(context)

        def setup_for_execution(self, context: InitResourceContext) -> None:
            log.append("MyMultiwriteIOManager setup_for_execution")

        def teardown_after_execution(self, context: InitResourceContext) -> None:
            log.append("MyMultiwriteIOManager teardown_after_execution")

    class ConfigIOManager(dg.ConfigurableIOManager):
        path_prefix: list[str]

        def setup_for_execution(self, context: InitResourceContext) -> None:
            log.append("ConfigIOManager setup_for_execution")

        def teardown_after_execution(self, context: InitResourceContext) -> None:
            log.append("ConfigIOManager teardown_after_execution")

        def handle_output(self, context, obj) -> None:
            log.append(
                "ConfigIOManager handle_output "
                + "/".join(self.path_prefix + list(context.asset_key.path))
            )

        def load_input(self, context) -> Any:
            log.append(
                "ConfigIOManager load_input "
                + "/".join(self.path_prefix + list(context.asset_key.path))
            )
            return "foo"

    class RawIOManager(dg.IOManager):
        def handle_output(self, context, obj) -> None:
            log.append("RawIOManager handle_output " + "/".join(list(context.asset_key.path)))

        def load_input(self, context) -> Any:
            log.append("RawIOManager load_input " + "/".join(list(context.asset_key.path)))
            return "foo"

    @dg.asset
    def my_asset() -> str:
        return "foo"

    @dg.asset
    def my_downstream_asset(my_asset: str) -> str:
        return my_asset + "bar"

    defs = dg.Definitions(
        assets=[my_asset, my_downstream_asset],
        resources={
            "io_manager": MyMultiwriteIOManager(
                base_io_manager=ConfigIOManager(path_prefix=["base"]),
                mirror_io_manager=RawIOManager(),
            ),
        },
    )

    assert defs.resolve_implicit_global_asset_job_def().execute_in_process().success
    assert log == [
        "ConfigIOManager setup_for_execution",
        "MyMultiwriteIOManager setup_for_execution",
        "ConfigIOManager handle_output base/my_asset",
        "RawIOManager handle_output my_asset",
        "ConfigIOManager load_input base/my_asset",
        "ConfigIOManager handle_output base/my_downstream_asset",
        "RawIOManager handle_output my_downstream_asset",
        "MyMultiwriteIOManager teardown_after_execution",
        "ConfigIOManager teardown_after_execution",
    ]


def test_nested_resource_raw_value_io_manager_with_cm_setup_teardown() -> None:
    log = []

    class MyMultiwriteIOManager(dg.ConfigurableIOManager):
        base_io_manager: dg.ResourceDependency[dg.IOManager]
        mirror_io_manager: dg.ResourceDependency[dg.IOManager]

        def handle_output(self, context, obj) -> None:
            self.base_io_manager.handle_output(context, obj)
            self.mirror_io_manager.handle_output(context, obj)

        def load_input(self, context) -> Any:
            return self.base_io_manager.load_input(context)

        def setup_for_execution(self, context: InitResourceContext) -> None:
            log.append("MyMultiwriteIOManager setup_for_execution")

        def teardown_after_execution(self, context: InitResourceContext) -> None:
            log.append("MyMultiwriteIOManager teardown_after_execution")

    class ConfigIOManager(dg.ConfigurableIOManager):
        path_prefix: list[str]

        @contextlib.contextmanager
        def yield_for_execution(self, context: InitResourceContext):
            log.append("ConfigIOManager cm setup")
            yield self
            log.append("ConfigIOManager cm teardown")

        def handle_output(self, context, obj) -> None:
            log.append(
                "ConfigIOManager handle_output "
                + "/".join(self.path_prefix + list(context.asset_key.path))
            )

        def load_input(self, context) -> Any:
            log.append(
                "ConfigIOManager load_input "
                + "/".join(self.path_prefix + list(context.asset_key.path))
            )
            return "foo"

    class RawIOManager(dg.IOManager):
        def handle_output(self, context, obj) -> None:
            log.append("RawIOManager handle_output " + "/".join(list(context.asset_key.path)))

        def load_input(self, context) -> Any:
            log.append("RawIOManager load_input " + "/".join(list(context.asset_key.path)))
            return "foo"

    @dg.resource
    @contextlib.contextmanager
    def raw_io_manager(context):
        log.append("RawIOManager cm setup")
        yield RawIOManager()
        log.append("RawIOManager cm teardown")

    @dg.asset
    def my_asset() -> str:
        return "foo"

    @dg.asset
    def my_downstream_asset(my_asset: str) -> str:
        return my_asset + "bar"

    defs = dg.Definitions(
        assets=[my_asset, my_downstream_asset],
        resources={
            "io_manager": MyMultiwriteIOManager(
                base_io_manager=ConfigIOManager(path_prefix=["base"]),
                mirror_io_manager=raw_io_manager,
            ),
        },
    )

    assert defs.resolve_implicit_global_asset_job_def().execute_in_process().success
    assert log == [
        "ConfigIOManager cm setup",
        "RawIOManager cm setup",
        "MyMultiwriteIOManager setup_for_execution",
        "ConfigIOManager handle_output base/my_asset",
        "RawIOManager handle_output my_asset",
        "ConfigIOManager load_input base/my_asset",
        "ConfigIOManager handle_output base/my_downstream_asset",
        "RawIOManager handle_output my_downstream_asset",
        "MyMultiwriteIOManager teardown_after_execution",
        "RawIOManager cm teardown",
        "ConfigIOManager cm teardown",
    ]


def test_multiple_nested_optional_resources() -> None:
    class InnerResource(dg.ConfigurableResource):
        a_string: str = "foo"

    class OuterResource(dg.ConfigurableResource):
        inner: InnerResource

    class MainResource(dg.ConfigurableResource):
        outer: Optional[OuterResource]

    executed = {}

    @dg.asset
    def expects_none_asset(main: MainResource):
        assert main.outer is None
        executed["expects_none_asset"] = True

    assert (
        dg.Definitions(
            assets=[expects_none_asset],
            resources={"main": MainResource(outer=None)},
        )
        .resolve_implicit_global_asset_job_def()
        .execute_in_process()
        .success
    )
    assert executed["expects_none_asset"]

    @dg.asset
    def hello_world_asset(main: MainResource):
        assert main.outer and main.outer.inner.a_string == "foo"
        executed["hello_world_asset"] = True

    assert (
        dg.Definitions(
            assets=[hello_world_asset],
            resources={"main": MainResource(outer=OuterResource(inner=InnerResource()))},
        )
        .resolve_implicit_global_asset_job_def()
        .execute_in_process()
        .success
    )
    assert executed["hello_world_asset"]


def test_multiple_nested_optional_resources_complex() -> None:
    class InnermostResource(dg.ConfigurableResource):
        a_string: str = "foo"

    class InnerResource(dg.ConfigurableResource):
        innermost: Optional[InnermostResource]

    class OuterResource(dg.ConfigurableResource):
        inner: Optional[InnerResource]

    class MainResource(dg.ConfigurableResource):
        outer: Optional[OuterResource]

    executed = {}

    @dg.asset
    def my_asset(main: MainResource):
        if main.outer and main.outer.inner and main.outer.inner.innermost:
            executed["my_asset"] = main.outer.inner.innermost.a_string
        else:
            executed["my_asset"] = None

    for main_resource in [
        MainResource(outer=None),
        MainResource(outer=OuterResource(inner=None)),
        MainResource(outer=OuterResource(inner=InnerResource(innermost=None))),
    ]:
        assert (
            dg.Definitions(
                assets=[my_asset],
                resources={"main": main_resource},
            )
            .resolve_implicit_global_asset_job_def()
            .execute_in_process()
            .success
        )
        assert executed["my_asset"] is None
        executed.clear()

    main_resource = MainResource(
        outer=OuterResource(inner=InnerResource(innermost=InnermostResource(a_string="bar")))
    )
    assert (
        dg.Definitions(
            assets=[my_asset],
            resources={"main": main_resource},
        )
        .resolve_implicit_global_asset_job_def()
        .execute_in_process()
        .success
    )
    assert executed["my_asset"] == "bar"
    executed.clear()


def test_nested_resource_setup_teardown_inner() -> None:
    log = []

    class MyBoringOuterResource(dg.ConfigurableResource):
        more_interesting_inner_resource: dg.ResourceDependency["SetupTeardownInnerResource"]

    class SetupTeardownInnerResource(dg.ConfigurableResource):
        def setup_for_execution(self, context: InitResourceContext) -> None:
            log.append("SetupTeardownInnerResource setup_for_execution")

        def teardown_after_execution(self, context: InitResourceContext) -> None:
            log.append("SetupTeardownInnerResource teardown_after_execution")

    @dg.asset
    def my_asset(outer: MyBoringOuterResource) -> str:
        log.append("my_asset")
        return "foo"

    defs = dg.Definitions(
        assets=[my_asset],
        resources={
            "outer": MyBoringOuterResource(
                more_interesting_inner_resource=SetupTeardownInnerResource()
            ),
        },
    )

    assert defs.resolve_implicit_global_asset_job_def().execute_in_process().success
    assert log == [
        "SetupTeardownInnerResource setup_for_execution",
        "my_asset",
        "SetupTeardownInnerResource teardown_after_execution",
    ]


def test_nested_resource_yield_inner() -> None:
    log = []

    class MyBoringOuterResource(dg.ConfigurableResource):
        more_interesting_inner_resource: dg.ResourceDependency["SetupTeardownInnerResource"]

    class SetupTeardownInnerResource(dg.ConfigurableResource):
        @contextlib.contextmanager
        def yield_for_execution(self, context: InitResourceContext):
            log.append("SetupTeardownInnerResource yield_for_execution")
            yield self
            log.append("SetupTeardownInnerResource yield_for_execution done")

    @dg.asset
    def my_asset(outer: MyBoringOuterResource) -> str:
        log.append("my_asset")
        return "foo"

    defs = dg.Definitions(
        assets=[my_asset],
        resources={
            "outer": MyBoringOuterResource(
                more_interesting_inner_resource=SetupTeardownInnerResource()
            ),
        },
    )

    assert defs.resolve_implicit_global_asset_job_def().execute_in_process().success
    assert log == [
        "SetupTeardownInnerResource yield_for_execution",
        "my_asset",
        "SetupTeardownInnerResource yield_for_execution done",
    ]


def test_nested_resources_runtime_config_fully_populated() -> None:
    """Ensures that nested resources which have default values for all
    fields can be overridden at runtime.
    """

    class InnermostResource(dg.ConfigurableResource):
        username: str = "default_username"
        password: str = "default_password"

    class NestedResource(dg.ConfigurableResource):
        creds: InnermostResource
        host: str = "default_host"
        database: str = "default_database"

    class TopLevelResource(dg.ConfigurableResource):
        config: NestedResource

    completed = {}

    @dg.asset
    def my_asset(db: TopLevelResource):
        assert db.config.creds.username == "foo"
        assert db.config.creds.password == "bar"
        assert db.config.host == "localhost"
        assert db.config.database == "my_db"
        completed["yes"] = True

    credentials = InnermostResource.configure_at_launch()
    db_config = NestedResource.configure_at_launch(creds=credentials)
    db = TopLevelResource(config=db_config)

    defs = dg.Definitions(
        assets=[my_asset],
        resources={
            "credentials": credentials,
            "db_config": db_config,
            "db": db,
        },
    )

    assert (
        defs.resolve_implicit_global_asset_job_def()
        .execute_in_process(
            {
                "resources": {
                    "credentials": {
                        "config": {
                            "username": "foo",
                            "password": "bar",
                        }
                    },
                    "db_config": {
                        "config": {
                            "host": "localhost",
                            "database": "my_db",
                        }
                    },
                }
            }
        )
        .success
    )
    assert completed["yes"]


def test_nested_resources_direct_config_fully_populated() -> None:
    """Covers regression introduced in pydantic v2.5.0 where union validation changed to Smart
    and direct initialization of nested ConfigurableResources stopped working.
    """
    import pydantic

    class Inner(dg.ConfigurableResource):
        b: str

    class Outer(dg.ConfigurableResource):
        a: str = pydantic.Field(default="a")
        inner: Inner

    # model_validate() on NESTED ConfigurableResource worked with pydantic v2.4.2, but stopped working on v2.5.0.
    result = Outer.model_validate(dict(a="a", inner=dict(b="b")))
    # >> TypeError: PartialResource.__init__() got an unexpected keyword argument 'b'
    assert isinstance(result, Outer)
    assert result.a == "a"
    assert isinstance(result.inner, Inner)
    assert result.inner.b == "b"
