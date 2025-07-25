import re

import dagster as dg
import dagster._check as check
import pytest
from dagster._api.snapshot_execution_plan import (
    gen_external_execution_plan_grpc,
    sync_get_external_execution_plan_grpc,
)
from dagster._core.definitions.selector import JobSubsetSelector
from dagster._core.errors import DagsterUserCodeProcessError
from dagster._core.instance import DagsterInstance
from dagster._core.remote_representation.external import RemoteExecutionPlan
from dagster._core.remote_representation.handle import JobHandle
from dagster._core.snap.execution_plan_snapshot import ExecutionPlanSnapshot

from dagster_tests.api_tests.utils import get_bar_repo_code_location, get_bar_workspace


@pytest.mark.asyncio
async def test_async_execution_plan_grpc(instance: DagsterInstance):
    with get_bar_repo_code_location(instance) as code_location:
        job_handle = JobHandle("foo", code_location.get_repository("bar_repo").handle)
        api_client = code_location.client

        execution_plan_snapshot = await gen_external_execution_plan_grpc(
            api_client,
            job_handle.get_remote_origin(),
            run_config={},
            job_snapshot_id="12345",
        )

        assert isinstance(execution_plan_snapshot, ExecutionPlanSnapshot)
        assert execution_plan_snapshot.step_keys_to_execute == [
            "do_something",
            "do_input",
        ]
        assert len(execution_plan_snapshot.steps) == 2

        job = code_location.get_job(
            JobSubsetSelector(
                location_name=code_location.name,
                repository_name="bar_repo",
                job_name="foo",
                op_selection=None,
                asset_selection=None,
            )
        )

        assert (
            code_location.get_execution_plan(
                job, run_config={}, step_keys_to_execute=None, known_state=None
            ).execution_plan_snapshot
            == (
                await code_location.gen_execution_plan(
                    job, run_config={}, step_keys_to_execute=None, known_state=None
                )
            ).execution_plan_snapshot
        )


@pytest.mark.asyncio
async def test_execution_plan_loader(instance: DagsterInstance):
    with get_bar_workspace(instance) as workspace:
        foo_selector = JobSubsetSelector(
            location_name="bar_code_location",
            repository_name="bar_repo",
            job_name="foo",
            op_selection=None,
            asset_selection=None,
        )
        foo_selector_with_subset = JobSubsetSelector(
            location_name="bar_code_location",
            repository_name="bar_repo",
            job_name="foo",
            op_selection=["do_something"],
            asset_selection=None,
        )

        bar_selector = JobSubsetSelector(
            location_name="bar_code_location",
            repository_name="bar_repo",
            job_name="bar",
            op_selection=None,
            asset_selection=None,
        )

        execution_plans = list(
            await RemoteExecutionPlan.gen_many(
                workspace,
                [
                    foo_selector,
                    foo_selector,
                    foo_selector_with_subset,
                    bar_selector,
                ],
            )
        )

        assert check.not_none(execution_plans[0]).execution_plan_snapshot.step_keys_to_execute == [
            "do_something",
            "do_input",
        ]
        assert execution_plans[0] is execution_plans[1]

        assert check.not_none(execution_plans[2]).execution_plan_snapshot.step_keys_to_execute == [
            "do_something",
        ]

        assert check.not_none(execution_plans[3]).execution_plan_snapshot.step_keys_to_execute == [
            "one",
            "fail_subset",
        ]


@pytest.mark.asyncio
async def test_async_execution_plan_error_grpc(instance: DagsterInstance):
    with get_bar_repo_code_location(instance) as code_location:
        job_handle = JobHandle("foo", code_location.get_repository("bar_repo").handle)
        api_client = code_location.client

        with pytest.raises(
            DagsterUserCodeProcessError,
            match=re.escape(
                "AssetKey(s) ['fake'] were selected, but no AssetsDefinition objects supply these keys."
            ),
        ):
            await gen_external_execution_plan_grpc(
                api_client,
                job_handle.get_remote_origin(),
                run_config={},
                asset_selection={dg.AssetKey("fake")},
                job_snapshot_id="12345",
            )


def test_execution_plan_error_grpc(instance: DagsterInstance):
    with get_bar_repo_code_location(instance) as code_location:
        job_handle = JobHandle("foo", code_location.get_repository("bar_repo").handle)
        api_client = code_location.client

        with pytest.raises(
            DagsterUserCodeProcessError,
            match=re.escape(
                "AssetKey(s) ['fake'] were selected, but no AssetsDefinition objects supply these keys."
            ),
        ):
            sync_get_external_execution_plan_grpc(
                api_client,
                job_handle.get_remote_origin(),
                run_config={},
                asset_selection={dg.AssetKey("fake")},
                job_snapshot_id="12345",
            )


def test_execution_plan_snapshot_api_grpc(instance: DagsterInstance):
    with get_bar_repo_code_location(instance) as code_location:
        job_handle = JobHandle("foo", code_location.get_repository("bar_repo").handle)
        api_client = code_location.client

        execution_plan_snapshot = sync_get_external_execution_plan_grpc(
            api_client,
            job_handle.get_remote_origin(),
            run_config={},
            job_snapshot_id="12345",
        )

        assert isinstance(execution_plan_snapshot, ExecutionPlanSnapshot)
        assert execution_plan_snapshot.step_keys_to_execute == [
            "do_something",
            "do_input",
        ]
        assert len(execution_plan_snapshot.steps) == 2


def test_execution_plan_with_step_keys_to_execute_snapshot_api_grpc(instance: DagsterInstance):
    with get_bar_repo_code_location(instance) as code_location:
        job_handle = JobHandle("foo", code_location.get_repository("bar_repo").handle)
        api_client = code_location.client

        execution_plan_snapshot = sync_get_external_execution_plan_grpc(
            api_client,
            job_handle.get_remote_origin(),
            run_config={},
            job_snapshot_id="12345",
            step_keys_to_execute=["do_something"],
        )

        assert isinstance(execution_plan_snapshot, ExecutionPlanSnapshot)
        assert execution_plan_snapshot.step_keys_to_execute == [
            "do_something",
        ]
        assert len(execution_plan_snapshot.steps) == 2


def test_execution_plan_with_subset_snapshot_api_grpc(instance: DagsterInstance):
    with get_bar_repo_code_location(instance) as code_location:
        job_handle = JobHandle("foo", code_location.get_repository("bar_repo").handle)
        api_client = code_location.client

        execution_plan_snapshot = sync_get_external_execution_plan_grpc(
            api_client,
            job_handle.get_remote_origin(),
            run_config={"ops": {"do_input": {"inputs": {"x": {"value": "test"}}}}},
            job_snapshot_id="12345",
            op_selection=["do_input"],
        )

        assert isinstance(execution_plan_snapshot, ExecutionPlanSnapshot)
        assert execution_plan_snapshot.step_keys_to_execute == [
            "do_input",
        ]
        assert len(execution_plan_snapshot.steps) == 1
