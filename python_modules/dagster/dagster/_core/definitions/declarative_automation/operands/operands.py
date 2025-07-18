import datetime
from collections.abc import Mapping, Set
from typing import TYPE_CHECKING, Optional

from dagster_shared.serdes import whitelist_for_serdes
from dagster_shared.serdes.utils import SerializableTimeDelta

from dagster._core.asset_graph_view.entity_subset import EntitySubset
from dagster._core.definitions.asset_key import AssetCheckKey, AssetKey
from dagster._core.definitions.declarative_automation.automation_condition import (
    AutomationResult,
    BuiltinAutomationCondition,
)
from dagster._core.definitions.declarative_automation.automation_context import AutomationContext
from dagster._core.definitions.declarative_automation.operands.subset_automation_condition import (
    SubsetAutomationCondition,
)
from dagster._record import record
from dagster._utils.schedules import reverse_cron_string_iterator

if TYPE_CHECKING:
    from dagster._core.storage.dagster_run import RunRecord


@whitelist_for_serdes
@record
class CodeVersionChangedCondition(BuiltinAutomationCondition[AssetKey]):
    @property
    def name(self) -> str:
        return "code_version_changed"

    def evaluate(self, context: AutomationContext) -> AutomationResult[AssetKey]:
        previous_code_version = context.cursor
        current_code_version = context.asset_graph.get(context.key).code_version
        if previous_code_version is None or previous_code_version == current_code_version:
            true_subset = context.get_empty_subset()
        else:
            true_subset = context.candidate_subset

        return AutomationResult(context, true_subset, cursor=current_code_version)


@record
@whitelist_for_serdes
class InitialEvaluationCondition(BuiltinAutomationCondition):
    """Condition to determine if this is the initial evaluation of a given AutomationCondition with a particular PartitionsDefinition."""

    @property
    def name(self) -> str:
        return "initial_evaluation"

    def _is_initial_evaluation(self, context: AutomationContext) -> bool:
        root_key = context.root_context.key
        previous_requested_subset = context.get_previous_requested_subset(root_key)
        if previous_requested_subset is None:
            return True

        previous_subset_value_type = type(previous_requested_subset.get_internal_value())

        current_subset = context.asset_graph_view.get_empty_subset(key=context.root_context.key)
        current_subset_value_type = type(current_subset.get_internal_value())
        return previous_subset_value_type != current_subset_value_type

    def evaluate(self, context: AutomationContext) -> AutomationResult:
        # we retain the condition_tree_id as a cursor despite it being unused as
        # earlier iterations of this condition used it and we want to retain the
        # option value of reverting this in the future
        condition_tree_id = context.root_context.condition.get_unique_id()
        if self._is_initial_evaluation(context):
            subset = context.candidate_subset
        else:
            subset = context.get_empty_subset()
        return AutomationResult(context, subset, cursor=condition_tree_id)


@whitelist_for_serdes
@record
class MissingAutomationCondition(SubsetAutomationCondition):
    @property
    def name(self) -> str:
        return "missing"

    async def compute_subset(self, context: AutomationContext) -> EntitySubset:  # pyright: ignore[reportIncompatibleMethodOverride]
        return await context.asset_graph_view.compute_missing_subset(
            key=context.key, from_subset=context.candidate_subset
        )


@whitelist_for_serdes(storage_name="InProgressAutomationCondition")
@record
class RunInProgressAutomationCondition(SubsetAutomationCondition):
    @property
    def name(self) -> str:
        return "run_in_progress"

    async def compute_subset(self, context: AutomationContext) -> EntitySubset:  # pyright: ignore[reportIncompatibleMethodOverride]
        return await context.asset_graph_view.compute_run_in_progress_subset(key=context.key)


@whitelist_for_serdes
@record
class BackfillInProgressAutomationCondition(SubsetAutomationCondition):
    @property
    def name(self) -> str:
        return "backfill_in_progress"

    async def compute_subset(self, context: AutomationContext) -> EntitySubset:  # pyright: ignore[reportIncompatibleMethodOverride]
        return await context.asset_graph_view.compute_backfill_in_progress_subset(key=context.key)


@whitelist_for_serdes(storage_name="FailedAutomationCondition")
@record
class ExecutionFailedAutomationCondition(SubsetAutomationCondition):
    @property
    def name(self) -> str:
        return "execution_failed"

    async def compute_subset(self, context: AutomationContext) -> EntitySubset:  # pyright: ignore[reportIncompatibleMethodOverride]
        return await context.asset_graph_view.compute_execution_failed_subset(key=context.key)


@whitelist_for_serdes
@record
class WillBeRequestedCondition(SubsetAutomationCondition):
    @property
    def description(self) -> str:
        return "Will be requested this tick"

    @property
    def name(self) -> str:
        return "will_be_requested"

    def _executable_with_root_context_key(self, context: AutomationContext) -> bool:
        # TODO: once we can launch backfills via the asset daemon, this can be removed
        from dagster._core.definitions.assets.graph.asset_graph import executable_in_same_run

        root_key = context.root_context.key
        return executable_in_same_run(
            asset_graph=context.asset_graph_view.asset_graph,
            child_key=root_key,
            parent_key=context.key,
        )

    def compute_subset(self, context: AutomationContext) -> EntitySubset:
        current_result = context.request_subsets_by_key.get(context.key)
        if current_result and self._executable_with_root_context_key(context):
            return current_result
        else:
            return context.get_empty_subset()


@whitelist_for_serdes
@record
class NewlyRequestedCondition(SubsetAutomationCondition):
    @property
    def name(self) -> str:
        return "newly_requested"

    def compute_subset(self, context: AutomationContext) -> EntitySubset:
        return context.get_previous_requested_subset(context.key) or context.get_empty_subset()


@whitelist_for_serdes
@record
class LatestRunExecutedWithRootTargetCondition(SubsetAutomationCondition):
    @property
    def name(self) -> str:
        return "executed_with_root_target"

    async def compute_subset(self, context: AutomationContext) -> EntitySubset:  # pyright: ignore[reportIncompatibleMethodOverride]
        def _filter_fn(run_record: "RunRecord") -> bool:
            if context.key == context.root_context.key:
                # this happens when this is evaluated for a self-dependent asset. in these cases,
                # it does not make sense to consider the asset as having been executed with itself
                # as the partition key of the target is necessarily different than the partition
                # key of the query key
                return False
            asset_selection = run_record.dagster_run.asset_selection or set()
            check_selection = run_record.dagster_run.asset_check_selection or set()
            return context.root_context.key in (asset_selection | check_selection)

        return await context.asset_graph_view.compute_latest_run_matches_subset(
            from_subset=context.candidate_subset, filter_fn=_filter_fn
        )


@whitelist_for_serdes
@record
class LatestRunExecutedWithTagsCondition(SubsetAutomationCondition):
    tag_keys: Optional[Set[str]] = None
    tag_values: Optional[Mapping[str, str]] = None

    @property
    def name(self) -> str:
        name = "executed_with_tags"
        props = []
        if self.tag_keys is not None:
            tag_key_str = ",".join(sorted(self.tag_keys))
            props.append(f"tag_keys={{{tag_key_str}}}")
        if self.tag_values is not None:
            tag_value_str = ",".join(
                [f"{key}:{value}" for key, value in sorted(self.tag_values.items())]
            )
            props.append(f"tag_values={{{tag_value_str}}}")

        if props:
            name += f"({', '.join(props)})"
        return name

    async def compute_subset(self, context: AutomationContext) -> EntitySubset:  # pyright: ignore[reportIncompatibleMethodOverride]
        def _filter_fn(run_record: "RunRecord") -> bool:
            if self.tag_keys and not all(
                key in run_record.dagster_run.tags for key in self.tag_keys
            ):
                return False
            if self.tag_values and not all(
                run_record.dagster_run.tags.get(key) == value
                for key, value in self.tag_values.items()
            ):
                return False
            return True

        return await context.asset_graph_view.compute_latest_run_matches_subset(
            from_subset=context.candidate_subset, filter_fn=_filter_fn
        )


@whitelist_for_serdes
@record
class NewlyUpdatedCondition(SubsetAutomationCondition):
    @property
    def name(self) -> str:
        return "newly_updated"

    async def compute_subset(self, context: AutomationContext) -> EntitySubset:  # pyright: ignore[reportIncompatibleMethodOverride]
        # if it's the first time evaluating, just return the empty subset
        if context.previous_temporal_context is None:
            return context.get_empty_subset()
        return await context.asset_graph_view.compute_updated_since_temporal_context_subset(
            key=context.key, temporal_context=context.previous_temporal_context
        )


@whitelist_for_serdes
@record
class DataVersionChangedCondition(SubsetAutomationCondition):
    @property
    def name(self) -> str:
        return "data_version_changed"

    async def compute_subset(self, context: AutomationContext) -> EntitySubset:  # pyright: ignore[reportIncompatibleMethodOverride]
        # if it's the first time evaluating, just return the empty subset
        if context.previous_temporal_context is None:
            return context.get_empty_subset()
        return await context.asset_graph_view.compute_data_version_changed_since_temporal_context_subset(
            key=context.key, temporal_context=context.previous_temporal_context
        )


@whitelist_for_serdes
@record
class CronTickPassedCondition(SubsetAutomationCondition):
    cron_schedule: str
    cron_timezone: str

    @property
    def name(self) -> str:
        return f"cron_tick_passed(cron_schedule={self.cron_schedule}, cron_timezone={self.cron_timezone})"

    def _get_previous_cron_tick(self, effective_dt: datetime.datetime) -> datetime.datetime:
        previous_ticks = reverse_cron_string_iterator(
            end_timestamp=effective_dt.timestamp(),
            cron_string=self.cron_schedule,
            execution_timezone=self.cron_timezone,
        )
        return next(previous_ticks)

    def compute_subset(self, context: AutomationContext) -> EntitySubset:
        previous_cron_tick = self._get_previous_cron_tick(context.evaluation_time)
        if (
            # no previous evaluation
            context.previous_evaluation_time is None
            # cron tick was not newly passed
            or previous_cron_tick < context.previous_evaluation_time
        ):
            return context.get_empty_subset()
        else:
            return context.candidate_subset


@whitelist_for_serdes
@record
class InLatestTimeWindowCondition(SubsetAutomationCondition):
    serializable_lookback_timedelta: Optional[SerializableTimeDelta] = None

    @staticmethod
    def from_lookback_delta(
        lookback_delta: Optional[datetime.timedelta],
    ) -> "InLatestTimeWindowCondition":
        return InLatestTimeWindowCondition(
            serializable_lookback_timedelta=SerializableTimeDelta.from_timedelta(lookback_delta)
            if lookback_delta
            else None
        )

    @property
    def lookback_timedelta(self) -> Optional[datetime.timedelta]:
        return (
            self.serializable_lookback_timedelta.to_timedelta()
            if self.serializable_lookback_timedelta
            else None
        )

    @property
    def description(self) -> str:
        return (
            f"Within {self.lookback_timedelta} of the end of the latest time window"
            if self.lookback_timedelta
            else "Within latest time window"
        )

    @property
    def name(self) -> str:
        name = "in_latest_time_window"
        if self.serializable_lookback_timedelta:
            name += f"(lookback_timedelta={self.lookback_timedelta})"
        return name

    def compute_subset(self, context: AutomationContext) -> EntitySubset:
        return context.asset_graph_view.compute_latest_time_window_subset(
            context.key, lookback_delta=self.lookback_timedelta
        )


@whitelist_for_serdes
@record
class CheckResultCondition(SubsetAutomationCondition[AssetCheckKey]):
    passed: bool

    @property
    def name(self) -> str:
        return "check_passed" if self.passed else "check_failed"

    async def compute_subset(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, context: AutomationContext[AssetCheckKey]
    ) -> EntitySubset[AssetCheckKey]:
        from dagster._core.storage.asset_check_execution_record import (
            AssetCheckExecutionResolvedStatus,
        )

        target_status = (
            AssetCheckExecutionResolvedStatus.SUCCEEDED
            if self.passed
            else AssetCheckExecutionResolvedStatus.FAILED
        )
        return await context.asset_graph_view.compute_subset_with_status(
            key=context.key, status=target_status
        )
