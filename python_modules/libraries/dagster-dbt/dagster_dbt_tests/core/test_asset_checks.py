import os
from typing import Any, Optional, cast

import pytest
from dagster import (
    AssetCheckEvaluation,
    AssetCheckKey,
    AssetCheckResult,
    AssetCheckSeverity,
    AssetCheckSpec,
    AssetExecutionContext,
    AssetKey,
    AssetsDefinition,
    AssetSelection,
    ExecuteInProcessResult,
    OpExecutionContext,
    Output,
    _check as check,
    asset_check,
    job,
    materialize,
    op,
)
from dagster_dbt.asset_decorator import dbt_assets
from dagster_dbt.asset_utils import DAGSTER_DBT_UNIQUE_ID_METADATA_KEY
from dagster_dbt.core.resource import DbtCliResource
from dagster_dbt.dagster_dbt_translator import DagsterDbtTranslator, DagsterDbtTranslatorSettings

from dagster_dbt_tests.dbt_projects import test_asset_checks_path, test_dbt_alias_path

pytest.importorskip("dbt.version", "1.6")

dagster_dbt_translator_with_checks = DagsterDbtTranslator(
    settings=DagsterDbtTranslatorSettings(enable_asset_checks=True)
)
dagster_dbt_translator_without_checks = DagsterDbtTranslator(
    settings=DagsterDbtTranslatorSettings(enable_asset_checks=False)
)
dagster_dbt_translator_with_checks_and_source_checks = DagsterDbtTranslator(
    settings=DagsterDbtTranslatorSettings(enable_source_tests_as_checks=True)
)


@pytest.fixture(params=[[["build"]], [["seed"], ["run"], ["test"]]], ids=["build", "seed-run-test"])
def dbt_commands(request):
    return request.param


def _get_select_args(dbt_cli_invocation) -> set[str]:
    *_, dbt_select_flag, dbt_select_args = list(dbt_cli_invocation.process.args)
    assert dbt_select_flag == "--select"
    return set(dbt_select_args.split())


def test_without_asset_checks(test_asset_checks_manifest: dict[str, Any]) -> None:
    @dbt_assets(
        manifest=test_asset_checks_manifest,
        dagster_dbt_translator=dagster_dbt_translator_without_checks,
    )
    def my_dbt_assets(context: AssetExecutionContext, dbt: DbtCliResource):
        yield from dbt.cli(["build"], context=context).stream()

    # dbt tests are present, but are not modeled as Dagster asset checks
    assert any(
        unique_id.startswith("test") for unique_id in test_asset_checks_manifest["nodes"].keys()
    )
    assert not my_dbt_assets.check_specs_by_output_name

    result = materialize(
        [my_dbt_assets],
        resources={"dbt": DbtCliResource(project_dir=os.fspath(test_asset_checks_path))},
        raise_on_error=False,
    )

    assert result.get_asset_observation_events()
    assert not result.get_asset_check_evaluations()


def test_customized_checks(test_asset_checks_manifest: dict[str, Any]) -> None:
    class CustomDagsterDbtTranslatorWithChecks(DagsterDbtTranslator):
        def get_asset_check_spec(self, asset_spec, manifest, unique_id, project):
            default_spec = super().get_asset_check_spec(asset_spec, manifest, unique_id, project)
            return default_spec._replace(description="blah") if default_spec else None

    @dbt_assets(
        manifest=test_asset_checks_manifest,
        dagster_dbt_translator=CustomDagsterDbtTranslatorWithChecks(),
    )
    def my_dbt_assets(context: AssetExecutionContext, dbt: DbtCliResource):
        yield from dbt.cli(["build"], context=context).stream()

    check_specs = list(my_dbt_assets.check_specs_by_output_name.values())
    assert len(check_specs) == 26
    for spec in check_specs:
        assert spec.description == "blah"


@pytest.mark.parametrize(
    "block_on_error_severity",
    [True, False],
)
def test_asset_checks_enabled_by_default(
    test_asset_checks_manifest: dict[str, Any], block_on_error_severity: bool
) -> None:
    class CustomDagsterDbtTranslatorWithNonBlockingChecks(DagsterDbtTranslator):
        def get_asset_check_spec(self, asset_spec, manifest, unique_id, project):
            default_check_spec = super().get_asset_check_spec(
                asset_spec, manifest, unique_id, project
            )
            return default_check_spec._replace(blocking=False) if default_check_spec else None

    @dbt_assets(
        manifest=test_asset_checks_manifest,
        dagster_dbt_translator=CustomDagsterDbtTranslatorWithNonBlockingChecks()
        if not block_on_error_severity
        else None,  # block_on_error_severity is the default
    )
    def my_dbt_assets(context: AssetExecutionContext, dbt: DbtCliResource):
        yield from dbt.cli(["build"], context=context).stream()

    assert any(
        unique_id.startswith("test") for unique_id in test_asset_checks_manifest["nodes"].keys()
    )
    assert my_dbt_assets.check_specs_by_output_name

    # too annoying to manually input all of the individual metadata fields
    stripped_specs_by_output_name = {}
    for output_name, spec in my_dbt_assets.check_specs_by_output_name.items():
        assert spec.metadata and spec.metadata[DAGSTER_DBT_UNIQUE_ID_METADATA_KEY]
        stripped_specs_by_output_name[output_name] = spec._replace(metadata={})

    assert stripped_specs_by_output_name == {
        "customers_not_null_customers_customer_id": AssetCheckSpec(
            name="not_null_customers_customer_id",
            asset=AssetKey(["customers"]),
            blocking=block_on_error_severity,
        ),
        "customers_unique_customers_customer_id": AssetCheckSpec(
            name="unique_customers_customer_id",
            asset=AssetKey(["customers"]),
            blocking=False,  # severity is warn
        ),
        "orders_accepted_values_orders_status__placed__shipped__completed__return_pending__returned": AssetCheckSpec(
            name="accepted_values_orders_status__placed__shipped__completed__return_pending__returned",
            asset=AssetKey(["orders"]),
            description="Status must be one of ['placed', 'shipped', 'completed', 'return_pending', or 'returned']",
            blocking=block_on_error_severity,
        ),
        "orders_not_null_orders_amount": AssetCheckSpec(
            name="not_null_orders_amount",
            asset=AssetKey(["orders"]),
            blocking=block_on_error_severity,
        ),
        "orders_not_null_orders_bank_transfer_amount": AssetCheckSpec(
            name="not_null_orders_bank_transfer_amount",
            asset=AssetKey(["orders"]),
            blocking=block_on_error_severity,
        ),
        "orders_not_null_orders_coupon_amount": AssetCheckSpec(
            name="not_null_orders_coupon_amount",
            asset=AssetKey(["orders"]),
            blocking=block_on_error_severity,
        ),
        "orders_not_null_orders_credit_card_amount": AssetCheckSpec(
            name="not_null_orders_credit_card_amount",
            asset=AssetKey(["orders"]),
            blocking=block_on_error_severity,
        ),
        "orders_not_null_orders_customer_id": AssetCheckSpec(
            name="not_null_orders_customer_id",
            asset=AssetKey(["orders"]),
            blocking=block_on_error_severity,
        ),
        "orders_not_null_orders_gift_card_amount": AssetCheckSpec(
            name="not_null_orders_gift_card_amount",
            asset=AssetKey(["orders"]),
            blocking=block_on_error_severity,
        ),
        "orders_not_null_orders_order_id": AssetCheckSpec(
            name="not_null_orders_order_id",
            asset=AssetKey(["orders"]),
            blocking=block_on_error_severity,
        ),
        "orders_relationships_orders_customer_id__customer_id__ref_customers_": AssetCheckSpec(
            name="relationships_orders_customer_id__customer_id__ref_customers_",
            asset=AssetKey(["orders"]),
            additional_deps=[
                AssetKey(["customers"]),
            ],
            blocking=block_on_error_severity,
        ),
        "orders_relationships_orders_customer_id__id__source_jaffle_shop_raw_customers_": AssetCheckSpec(
            name="relationships_orders_customer_id__id__source_jaffle_shop_raw_customers_",
            asset=AssetKey(["orders"]),
            additional_deps=[
                AssetKey(["jaffle_shop", "raw_customers"]),
            ],
            blocking=block_on_error_severity,
        ),
        "orders_relationships_with_duplicate_orders_ref_customers___customer_id__customer_id__ref_customers_": AssetCheckSpec(
            name="relationships_with_duplicate_orders_ref_customers___customer_id__customer_id__ref_customers_",
            asset=AssetKey(["orders"]),
            additional_deps=[
                AssetKey(["customers"]),
            ],
            blocking=block_on_error_severity,
        ),
        "orders_unique_orders_order_id": AssetCheckSpec(
            name="unique_orders_order_id",
            asset=AssetKey(["orders"]),
            blocking=block_on_error_severity,
        ),
        "stg_customers_not_null_stg_customers_customer_id": AssetCheckSpec(
            name="not_null_stg_customers_customer_id",
            asset=AssetKey(["stg_customers"]),
            blocking=block_on_error_severity,
        ),
        "stg_customers_unique_stg_customers_customer_id": AssetCheckSpec(
            name="unique_stg_customers_customer_id",
            asset=AssetKey(["stg_customers"]),
            blocking=block_on_error_severity,
        ),
        "stg_orders_accepted_values_stg_orders_status__placed__shipped__completed__return_pending__returned": AssetCheckSpec(
            name="accepted_values_stg_orders_status__placed__shipped__completed__return_pending__returned",
            asset=AssetKey(["stg_orders"]),
            blocking=block_on_error_severity,
        ),
        "stg_orders_not_null_stg_orders_order_id": AssetCheckSpec(
            name="not_null_stg_orders_order_id",
            asset=AssetKey(["stg_orders"]),
            blocking=block_on_error_severity,
        ),
        "stg_orders_unique_stg_orders_order_id": AssetCheckSpec(
            name="unique_stg_orders_order_id",
            asset=AssetKey(["stg_orders"]),
            blocking=block_on_error_severity,
        ),
        "stg_payments_accepted_values_stg_payments_payment_method__credit_card__coupon__bank_transfer__gift_card": AssetCheckSpec(
            name="accepted_values_stg_payments_payment_method__credit_card__coupon__bank_transfer__gift_card",
            asset=AssetKey(["stg_payments"]),
            blocking=block_on_error_severity,
        ),
        "stg_payments_not_null_stg_payments_payment_id": AssetCheckSpec(
            name="not_null_stg_payments_payment_id",
            asset=AssetKey(["stg_payments"]),
            blocking=block_on_error_severity,
        ),
        "stg_payments_unique_stg_payments_payment_id": AssetCheckSpec(
            name="unique_stg_payments_payment_id",
            asset=AssetKey(["stg_payments"]),
            blocking=block_on_error_severity,
        ),
        "fail_tests_model_accepted_values_fail_tests_model_first_name__foo__bar__baz": AssetCheckSpec(
            name="accepted_values_fail_tests_model_first_name__foo__bar__baz",
            asset=AssetKey(["fail_tests_model"]),
            blocking=block_on_error_severity,
        ),
        "fail_tests_model_unique_fail_tests_model_id": AssetCheckSpec(
            name="unique_fail_tests_model_id",
            asset=AssetKey(["fail_tests_model"]),
            blocking=block_on_error_severity,
        ),
        "customers_singular_test_with_single_dependency": AssetCheckSpec(
            name="singular_test_with_single_dependency",
            asset=AssetKey(["customers"]),
            blocking=block_on_error_severity,
        ),
        "customers_singular_test_with_meta_and_multiple_dependencies": AssetCheckSpec(
            name="singular_test_with_meta_and_multiple_dependencies",
            asset=AssetKey(["customers"]),
            additional_deps=[
                AssetKey(["orders"]),
            ],
            blocking=block_on_error_severity,
        ),
    }

    result = materialize(
        [my_dbt_assets],
        resources={"dbt": DbtCliResource(project_dir=os.fspath(test_asset_checks_path))},
        raise_on_error=False,
    )

    assert result.get_asset_observation_events()
    assert result.get_asset_check_evaluations()


def test_enable_asset_checks_with_custom_translator() -> None:
    class CustomDagsterDbtTranslatorWithInitNoSuper(DagsterDbtTranslator):
        def __init__(self, test_arg: str):
            self.test_arg = test_arg

    class CustomDagsterDbtTranslatorWithInitWithSuper(DagsterDbtTranslator):
        def __init__(self, test_arg: str):
            self.test_arg = test_arg

            super().__init__()

    class CustomDagsterDbtTranslator(DagsterDbtTranslator): ...

    class CustomDagsterDbtTranslatorWithPassThrough(DagsterDbtTranslator):
        def __init__(self, test_arg: str, *args, **kwargs):
            self.test_arg = test_arg

            super().__init__(*args, **kwargs)

    no_pass_through_no_super_translator = CustomDagsterDbtTranslatorWithInitNoSuper("test")
    assert no_pass_through_no_super_translator.settings.enable_asset_checks

    no_pass_through_with_super_translator = CustomDagsterDbtTranslatorWithInitWithSuper("test")
    assert no_pass_through_with_super_translator.settings.enable_asset_checks

    custom_translator = CustomDagsterDbtTranslator(
        settings=DagsterDbtTranslatorSettings(enable_asset_checks=False)
    )
    assert not custom_translator.settings.enable_asset_checks

    pass_through_translator = CustomDagsterDbtTranslatorWithPassThrough(
        "test",
        settings=DagsterDbtTranslatorSettings(enable_asset_checks=False),
    )
    assert not pass_through_translator.settings.enable_asset_checks


def _materialize_dbt_assets(
    manifest: dict[str, Any],
    dbt_commands: list[list[str]],
    selection: Optional[AssetSelection],
    expected_dbt_selection: Optional[set[str]] = None,
    dagster_dbt_translator=dagster_dbt_translator_with_checks,
    additional_assets: Optional[list[AssetsDefinition]] = None,
) -> ExecuteInProcessResult:
    @dbt_assets(manifest=manifest, dagster_dbt_translator=dagster_dbt_translator)
    def my_dbt_assets(context: AssetExecutionContext, dbt: DbtCliResource):
        for dbt_command in dbt_commands:
            cli_invocation = dbt.cli(dbt_command, context=context)
            assert (
                expected_dbt_selection is None
                or _get_select_args(cli_invocation) == expected_dbt_selection
            )
            yield from cli_invocation.stream()

    result = materialize(
        [my_dbt_assets] + (additional_assets or []),
        resources={"dbt": DbtCliResource(project_dir=os.fspath(test_asset_checks_path))},
        selection=selection,
        raise_on_error=False,
    )

    return result


def test_materialize_no_selection(
    test_asset_checks_manifest: dict[str, Any], dbt_commands: list[list[str]]
) -> None:
    result = _materialize_dbt_assets(
        test_asset_checks_manifest,
        dbt_commands,
        selection=None,
        expected_dbt_selection={"fqn:*"},
    )
    assert not result.success  # fail_tests_model fails
    # raw_customers is a source, and thus does not receive an asset materialization.
    assert len(result.get_asset_materialization_events()) == 10
    assert len(result.get_asset_check_evaluations()) == 26
    assert len(result.get_asset_observation_events()) == 4


def test_materialize_asset_and_checks(
    test_asset_checks_manifest: dict[str, Any], dbt_commands: list[list[str]]
) -> None:
    result = _materialize_dbt_assets(
        test_asset_checks_manifest,
        dbt_commands,
        selection=AssetSelection.assets(AssetKey(["customers"])),
        expected_dbt_selection={"test_dagster_asset_checks.customers"},
    )
    assert result.success
    assert len(result.get_asset_materialization_events()) == 1
    assert len(result.get_asset_check_evaluations()) == 4
    assert len(result.get_asset_observation_events()) == 6
    # no tests were excluded, so we include singular and relationship tests
    assert {
        (e.asset_key, e.asset_observation_data.asset_observation.metadata.get("unique_id").value)  # type: ignore[attr-defined]
        for e in result.get_asset_observation_events()
    } == {
        (
            AssetKey(["customers"]),
            "test.test_dagster_asset_checks.relationships_with_duplicate_orders_ref_customers___customer_id__customer_id__ref_customers_.d9e47ca78e",
        ),
        (
            AssetKey(["customers"]),
            "test.test_dagster_asset_checks.relationships_orders_customer_id__customer_id__ref_customers_.c6ec7f58f2",
        ),
        (
            AssetKey(["customers"]),
            "test.test_dagster_asset_checks.singular_test_with_no_meta_and_multiple_dependencies",
        ),
        (
            AssetKey(["orders"]),
            "test.test_dagster_asset_checks.relationships_with_duplicate_orders_ref_customers___customer_id__customer_id__ref_customers_.d9e47ca78e",
        ),
        (
            AssetKey(["orders"]),
            "test.test_dagster_asset_checks.relationships_orders_customer_id__customer_id__ref_customers_.c6ec7f58f2",
        ),
        (
            AssetKey(["orders"]),
            "test.test_dagster_asset_checks.singular_test_with_no_meta_and_multiple_dependencies",
        ),
    }


def test_materialize_asset_and_checks_with_python_check(
    test_asset_checks_manifest: dict[str, Any], dbt_commands: list[list[str]]
) -> None:
    @asset_check(asset=AssetKey(["customers"]))
    def my_python_check():
        return AssetCheckResult(passed=True)

    result = _materialize_dbt_assets(
        test_asset_checks_manifest,
        dbt_commands,
        selection=AssetSelection.assets(AssetKey(["customers"])),
        expected_dbt_selection={"test_dagster_asset_checks.customers"},
        additional_assets=[my_python_check],
    )
    assert result.success
    assert len(result.get_asset_materialization_events()) == 1
    assert len(result.get_asset_check_evaluations()) == 5
    assert "my_python_check" in {e.check_name for e in result.get_asset_check_evaluations()}
    assert len(result.get_asset_observation_events()) == 6


def test_materialize_asset_checks_disabled(
    test_asset_checks_manifest: dict[str, Any], dbt_commands: list[list[str]]
) -> None:
    result = _materialize_dbt_assets(
        test_asset_checks_manifest,
        dbt_commands,
        selection=AssetSelection.assets(AssetKey(["customers"])),
        expected_dbt_selection={"test_dagster_asset_checks.customers"},
        dagster_dbt_translator=dagster_dbt_translator_without_checks,
    )
    assert result.success
    assert len(result.get_asset_materialization_events()) == 1
    assert len(result.get_asset_check_evaluations()) == 0
    # observations above, plus observations for the check resuls. One extra because one test is a
    # relationship test
    assert len(result.get_asset_observation_events()) == 11


def test_materialize_asset_no_checks(
    test_asset_checks_manifest: dict[str, Any], dbt_commands: list[list[str]]
) -> None:
    result = _materialize_dbt_assets(
        test_asset_checks_manifest,
        dbt_commands,
        selection=AssetSelection.assets(AssetKey(["customers"])).without_checks(),
        expected_dbt_selection={"test_dagster_asset_checks.customers"},
    )
    assert result.success
    assert len(result.get_asset_materialization_events()) == 1
    assert len(result.get_asset_check_evaluations()) == 0
    # since checks are exclued, we don't run the singular or relationship tests
    assert len(result.get_asset_observation_events()) == 0


@pytest.mark.parametrize(
    "dagster_dbt_translator",
    [
        dagster_dbt_translator_with_checks,
        DagsterDbtTranslator(
            settings=DagsterDbtTranslatorSettings(
                enable_asset_checks=True, enable_dbt_selection_by_name=True
            )
        ),
    ],
)
def test_materialize_checks_no_asset(
    test_asset_checks_manifest: dict[str, Any],
    dbt_commands: list[list[str]],
    dagster_dbt_translator: DagsterDbtTranslator,
) -> None:
    expected_dbt_selection = {
        "test_dagster_asset_checks.not_null_customers_customer_id",
        "test_dagster_asset_checks.singular_test_with_meta_and_multiple_dependencies",
        "test_dagster_asset_checks.singular_test_with_single_dependency",
        "test_dagster_asset_checks.unique_customers_customer_id",
    }
    if dagster_dbt_translator.settings.enable_dbt_selection_by_name:
        expected_dbt_selection = {
            "not_null_customers_customer_id",
            "singular_test_with_meta_and_multiple_dependencies",
            "singular_test_with_single_dependency",
            "unique_customers_customer_id",
        }

    result = _materialize_dbt_assets(
        test_asset_checks_manifest,
        dbt_commands,
        selection=(
            AssetSelection.assets(AssetKey(["customers"]))
            - AssetSelection.assets(AssetKey(["customers"])).without_checks()
        ),
        expected_dbt_selection=expected_dbt_selection,
        dagster_dbt_translator=dagster_dbt_translator,
    )
    assert result.success
    assert len(result.get_asset_materialization_events()) == 0
    assert len(result.get_asset_check_evaluations()) == 4
    # since we're not materializing the asset, we can't use indirect selection and therefore
    # don't run the singular or relationship tests
    assert len(result.get_asset_observation_events()) == 0


def test_extra_checks(
    test_asset_checks_manifest: dict[str, Any], dbt_commands: list[list[str]]
) -> None:
    result = _materialize_dbt_assets(
        test_asset_checks_manifest,
        dbt_commands,
        selection=(
            AssetSelection.assets(AssetKey(["customers"]))
            | AssetSelection.checks(
                AssetCheckKey(AssetKey(["stg_orders"]), "unique_stg_orders_order_id")
            )
        ),
        expected_dbt_selection={
            "test_dagster_asset_checks.customers",
            "test_dagster_asset_checks.staging.unique_stg_orders_order_id",
        },
    )
    assert result.success
    assert len(result.get_asset_materialization_events()) == 1
    # 4 tests on customers, and unique_stg_orders_order_id
    assert len(result.get_asset_check_evaluations()) == 5
    # no tests were excluded, so we include singular and relationship tests
    assert len(result.get_asset_observation_events()) == 6


def test_asset_checks_results(
    test_asset_checks_manifest: dict[str, Any], dbt_commands: list[list[str]]
):
    @dbt_assets(
        manifest=test_asset_checks_manifest,
        dagster_dbt_translator=dagster_dbt_translator_with_checks,
    )
    def my_dbt_assets(context: AssetExecutionContext, dbt: DbtCliResource):
        events = []
        invocation_id = ""
        for dbt_command in dbt_commands:
            dbt_invocation = dbt.cli(dbt_command, context=context, raise_on_error=False)
            events += list(dbt_invocation.stream())
            invocation_id = dbt_invocation.get_artifact("run_results.json")["metadata"][
                "invocation_id"
            ]

        for event in events:
            if isinstance(event, AssetCheckResult):
                assert cast("int", event.metadata["Execution Duration"].value) > 0

        expected_results = [
            AssetCheckResult(
                passed=True,
                asset_key=AssetKey(["customers"]),
                check_name="unique_customers_customer_id",
                metadata={
                    "unique_id": (
                        "test.test_dagster_asset_checks.unique_customers_customer_id.c5af1ff4b1"
                    ),
                    "invocation_id": invocation_id,
                    "status": "pass",
                    "dagster_dbt/failed_row_count": 0,
                },
            ),
            AssetCheckResult(
                passed=True,
                asset_key=AssetKey(["customers"]),
                check_name="not_null_customers_customer_id",
                metadata={
                    "unique_id": (
                        "test.test_dagster_asset_checks.not_null_customers_customer_id.5c9bf9911d"
                    ),
                    "invocation_id": invocation_id,
                    "status": "pass",
                    "dagster_dbt/failed_row_count": 0,
                },
            ),
            AssetCheckResult(
                passed=False,
                asset_key=AssetKey(["fail_tests_model"]),
                check_name="unique_fail_tests_model_id",
                severity=AssetCheckSeverity.WARN,
                metadata={
                    "unique_id": (
                        "test.test_dagster_asset_checks.unique_fail_tests_model_id.1619308eb1"
                    ),
                    "invocation_id": invocation_id,
                    "status": "warn",
                    "dagster_dbt/failed_row_count": 1,
                },
            ),
            AssetCheckResult(
                passed=False,
                asset_key=AssetKey(["fail_tests_model"]),
                check_name="accepted_values_fail_tests_model_first_name__foo__bar__baz",
                severity=AssetCheckSeverity.ERROR,
                metadata={
                    "unique_id": (
                        "test.test_dagster_asset_checks.accepted_values_fail_tests_model_first_name__foo__bar__baz.5f958cf018"
                    ),
                    "invocation_id": invocation_id,
                    "status": "fail",
                    "dagster_dbt/failed_row_count": 4,
                },
            ),
        ]

        # filter these out for comparison
        non_deterministic_metadata_keys = ["Execution Duration"]
        check_events_without_non_deterministic_metadata = {}
        for event in events:
            if isinstance(event, AssetCheckResult):
                check_events_without_non_deterministic_metadata[
                    event.asset_key, event.check_name
                ] = event._replace(
                    metadata={
                        k: v
                        for k, v in event.metadata.items()
                        if k not in non_deterministic_metadata_keys
                    }
                )

        for expected_asset_check_result in expected_results:
            assert (
                check_events_without_non_deterministic_metadata[
                    expected_asset_check_result.asset_key, expected_asset_check_result.check_name
                ]
                == expected_asset_check_result
            )

        yield from events

    result = materialize(
        [my_dbt_assets],
        resources={"dbt": DbtCliResource(project_dir=os.fspath(test_asset_checks_path))},
        raise_on_error=False,
    )
    assert not result.success
    assert len(result.get_asset_materialization_events()) == 10
    assert len(result.get_asset_check_evaluations()) == 26
    assert len(result.get_step_failure_events()) == 1
    assert (
        "1 blocking asset check failed with ERROR severity:\\nfail_tests_model: accepted_values_fail_tests_model_first_name__foo__bar__baz"
        in str(result.get_step_failure_events()[0])
    )


def test_asset_checks_evaluations(
    test_asset_checks_manifest: dict[str, Any], dbt_commands: list[list[str]]
):
    @op
    def my_dbt_op(context: OpExecutionContext, dbt: DbtCliResource):
        events = []
        invocation_id = ""
        for dbt_command in dbt_commands:
            dbt_invocation = dbt.cli(
                dbt_command,
                context=context,
                raise_on_error=False,
                manifest=test_asset_checks_manifest,
                dagster_dbt_translator=dagster_dbt_translator_with_checks,
            )
            events += list(dbt_invocation.stream())
            invocation_id = dbt_invocation.get_artifact("run_results.json")["metadata"][
                "invocation_id"
            ]

        for event in events:
            if isinstance(event, AssetCheckEvaluation):
                assert cast("int", event.metadata["Execution Duration"].value) > 0

        # Sanity check that we don't have AssetCheckResult events when using an op
        assert not any([event for event in events if isinstance(event, AssetCheckResult)])

        expected_results = [
            AssetCheckEvaluation(
                passed=True,
                asset_key=AssetKey(["customers"]),
                check_name="unique_customers_customer_id",
                metadata={
                    "unique_id": (
                        "test.test_dagster_asset_checks.unique_customers_customer_id.c5af1ff4b1"
                    ),
                    "invocation_id": invocation_id,
                    "status": "pass",
                    "dagster_dbt/failed_row_count": 0,
                },
            ),
            AssetCheckEvaluation(
                passed=True,
                asset_key=AssetKey(["customers"]),
                check_name="not_null_customers_customer_id",
                metadata={
                    "unique_id": (
                        "test.test_dagster_asset_checks.not_null_customers_customer_id.5c9bf9911d"
                    ),
                    "invocation_id": invocation_id,
                    "status": "pass",
                    "dagster_dbt/failed_row_count": 0,
                },
            ),
            AssetCheckEvaluation(
                passed=False,
                asset_key=AssetKey(["fail_tests_model"]),
                check_name="unique_fail_tests_model_id",
                severity=AssetCheckSeverity.WARN,
                metadata={
                    "unique_id": (
                        "test.test_dagster_asset_checks.unique_fail_tests_model_id.1619308eb1"
                    ),
                    "invocation_id": invocation_id,
                    "status": "warn",
                    "dagster_dbt/failed_row_count": 1,
                },
            ),
            AssetCheckEvaluation(
                passed=False,
                asset_key=AssetKey(["fail_tests_model"]),
                check_name="accepted_values_fail_tests_model_first_name__foo__bar__baz",
                severity=AssetCheckSeverity.ERROR,
                metadata={
                    "unique_id": (
                        "test.test_dagster_asset_checks.accepted_values_fail_tests_model_first_name__foo__bar__baz.5f958cf018"
                    ),
                    "invocation_id": invocation_id,
                    "status": "fail",
                    "dagster_dbt/failed_row_count": 4,
                },
            ),
        ]

        # filter these out for comparison
        non_deterministic_metadata_keys = ["Execution Duration"]
        check_events_without_non_deterministic_metadata = {}
        for event in events:
            if isinstance(event, AssetCheckEvaluation):
                check_events_without_non_deterministic_metadata[
                    event.asset_key, event.check_name
                ] = event._replace(
                    metadata={
                        k: v
                        for k, v in event.metadata.items()
                        if k not in non_deterministic_metadata_keys
                    }
                )

        for expected_asset_check_evaluation in expected_results:
            assert (
                check_events_without_non_deterministic_metadata[
                    expected_asset_check_evaluation.asset_key,
                    expected_asset_check_evaluation.check_name,
                ]
                == expected_asset_check_evaluation
            )

        yield from events
        yield Output(None)

    @job
    def my_dbt_job():
        my_dbt_op()

    result = my_dbt_job.execute_in_process(
        resources={"dbt": DbtCliResource(project_dir=os.fspath(test_asset_checks_path))}
    )
    assert result.success


@pytest.mark.parametrize(
    "selection",
    ["customers", "tag:customer_info"],
)
def test_select_model_with_tests(
    test_asset_checks_manifest: dict[str, Any], dbt_commands: list[list[str]], selection: str
):
    @dbt_assets(
        manifest=test_asset_checks_manifest,
        select=selection,
        dagster_dbt_translator=dagster_dbt_translator_with_checks,
    )
    def my_dbt_assets(context: AssetExecutionContext, dbt: DbtCliResource):
        for dbt_command in dbt_commands:
            cli_invocation = dbt.cli(dbt_command, context=context)
            assert _get_select_args(cli_invocation) == {selection}
            yield from cli_invocation.stream()

    assert my_dbt_assets.keys == {AssetKey(["customers"])}
    assert my_dbt_assets.check_keys == {
        AssetCheckKey(asset_key=AssetKey(["customers"]), name="unique_customers_customer_id"),
        AssetCheckKey(asset_key=AssetKey(["customers"]), name="not_null_customers_customer_id"),
        AssetCheckKey(
            asset_key=AssetKey(["customers"]), name="singular_test_with_single_dependency"
        ),
        AssetCheckKey(
            asset_key=AssetKey(["customers"]),
            name="singular_test_with_meta_and_multiple_dependencies",
        ),
    }

    result = materialize(
        [my_dbt_assets],
        resources={"dbt": DbtCliResource(project_dir=os.fspath(test_asset_checks_path))},
    )

    assert result.success
    assert len(result.get_asset_materialization_events()) == 1
    assert len(result.get_asset_check_evaluations()) == 4
    # no tests were excluded, so we include singular and relationship tests
    assert len(result.get_asset_observation_events()) == 6
    assert {
        (e.asset_key, e.asset_observation_data.asset_observation.metadata.get("unique_id").value)  # type: ignore[attr-defined]
        for e in result.get_asset_observation_events()
    } == {
        (
            AssetKey(["customers"]),
            "test.test_dagster_asset_checks.relationships_with_duplicate_orders_ref_customers___customer_id__customer_id__ref_customers_.d9e47ca78e",
        ),
        (
            AssetKey(["customers"]),
            "test.test_dagster_asset_checks.relationships_orders_customer_id__customer_id__ref_customers_.c6ec7f58f2",
        ),
        (
            AssetKey(["customers"]),
            "test.test_dagster_asset_checks.singular_test_with_no_meta_and_multiple_dependencies",
        ),
        (
            AssetKey(["orders"]),
            "test.test_dagster_asset_checks.relationships_with_duplicate_orders_ref_customers___customer_id__customer_id__ref_customers_.d9e47ca78e",
        ),
        (
            AssetKey(["orders"]),
            "test.test_dagster_asset_checks.relationships_orders_customer_id__customer_id__ref_customers_.c6ec7f58f2",
        ),
        (
            AssetKey(["orders"]),
            "test.test_dagster_asset_checks.singular_test_with_no_meta_and_multiple_dependencies",
        ),
    }


def test_dbt_with_dotted_dependency_names(test_dbt_alias_manifest: dict[str, Any]) -> None:
    @dbt_assets(
        manifest=test_dbt_alias_manifest, dagster_dbt_translator=dagster_dbt_translator_with_checks
    )
    def my_dbt_assets(context: AssetExecutionContext, dbt: DbtCliResource):
        yield from dbt.cli(["build"], context=context).stream()

    result = materialize(
        [my_dbt_assets],
        resources={"dbt": DbtCliResource(project_dir=os.fspath(test_dbt_alias_path))},
    )
    assert result.success


def test_dbt_source_tests(
    test_asset_checks_manifest: dict[str, Any],
) -> None:
    """Test default behavior when dbt source tests are configured."""

    @dbt_assets(
        manifest=test_asset_checks_manifest,
        dagster_dbt_translator=dagster_dbt_translator_with_checks,
    )
    def my_dbt_assets(context: AssetExecutionContext, dbt: DbtCliResource):
        yield from dbt.cli(["build"], context=context).stream()

    result = materialize(
        [my_dbt_assets],
        resources={"dbt": DbtCliResource(project_dir=os.fspath(test_asset_checks_path))},
        raise_on_error=False,
    )
    assert not result.success
    asset_observations_for_raw_customers = [
        event.asset_observation_data.asset_observation
        for event in result.get_asset_observation_events()
        if event.asset_observation_data.asset_observation.asset_key
        == AssetKey(["jaffle_shop", "raw_customers"])
    ]
    assert len(asset_observations_for_raw_customers) == 2
    assert (
        len(
            [
                obs
                for obs in asset_observations_for_raw_customers
                if "source_not_null_jaffle_shop_raw_customers_id" in check.not_none(obs.description)
            ]
        )
        == 1
    )
    assert (
        len(
            [
                obs
                for obs in asset_observations_for_raw_customers
                if "source_unique_jaffle_shop_raw_customers_id" in check.not_none(obs.description)
            ]
        )
        == 1
    )


@pytest.mark.parametrize(
    "selection,expected_num_source_test_execs,success",
    [(None, 2, False), ("stg_customers", 2, True), ("stg_orders", 0, True)],
    ids=["select_all", "select_downstream_of_source", "select_non_source"],
)
def test_dbt_source_tests_checks_enabled(
    test_asset_checks_manifest: dict[str, Any],
    selection: Optional[str],
    expected_num_source_test_execs: int,
    success: bool,
) -> None:
    """Test behavior when dbt source tests are configured, but checks are disabled."""

    @dbt_assets(
        manifest=test_asset_checks_manifest,
        dagster_dbt_translator=dagster_dbt_translator_with_checks_and_source_checks,
    )
    def my_dbt_assets(context: AssetExecutionContext, dbt: DbtCliResource):
        yield from dbt.cli(["build"], context=context).stream()

    result = materialize(
        [my_dbt_assets],
        resources={"dbt": DbtCliResource(project_dir=os.fspath(test_asset_checks_path))},
        raise_on_error=False,
        selection=selection,
    )
    assert result.success == success
    asset_check_results = [
        eval_result.asset_key
        for eval_result in result.get_asset_check_evaluations()
        if eval_result.asset_key == AssetKey(["jaffle_shop", "raw_customers"])
    ]
    assert len(asset_check_results) == expected_num_source_test_execs
