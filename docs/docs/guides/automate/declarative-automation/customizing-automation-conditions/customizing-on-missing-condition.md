---
description: Example use cases for customizing AutomationCondition.on_missing()
sidebar_position: 200
title: Customizing on_missing
---

import ScaffoldAsset from '@site/docs/partials/\_ScaffoldAsset.md';

<ScaffoldAsset />

## Ignoring dependencies

By default, <PyObject module="dagster" section="assets" object="AutomationCondition.on_missing" displayText="AutomationCondition.on_missing()" /> will wait for all upstream dependencies to be materialized before executing the asset it's attached to.

In some cases, it can be useful to ignore some upstream dependencies in this calculation. This can be done by passing in an <PyObject section="assets" module="dagster" object="AssetSelection" /> to be ignored:

<CodeExample
  path="docs_snippets/docs_snippets/concepts/declarative_automation/on_missing/ignore_dependencies.py"
  title="src/<project_name>/defs/assets.py"
/>

Alternatively, you can pass in an <PyObject section="assets" module="dagster" object="AssetSelection" /> to be allowed:

<CodeExample
  path="docs_snippets/docs_snippets/concepts/declarative_automation/on_missing/allow_dependencies.py"
  title="src/<project_name>/defs/assets.py"
/>

## Waiting for all blocking asset checks to complete before executing

The `AutomationCondition.all_deps_blocking_checks_passed()` condition becomes true after all upstream blocking checks have passed.

This can be combined with `AutomationCondition.on_missing()` to ensure that your asset does not execute if upstream data is failing data quality checks:

<CodeExample
  path="docs_snippets/docs_snippets/concepts/declarative_automation/on_missing/blocking_checks_condition.py"
  title="src/<project_name>/defs/assets.py"
/>

## Updating older time partitions

By default, `AutomationCondition.on_missing()` will only update the latest time partition of an asset.

This means that the condition will not automatically "catch" up if upstream data is delayed for longer than it takes for a new partition to appear. If desired, this sub-condition can be removed or replaced:

<CodeExample
  path="docs_snippets/docs_snippets/concepts/declarative_automation/on_missing/update_older_time_partitions.py"
  title="src/<project_name>/defs/assets.py"
/>

Note that the above modifications will still not consider that were added to the asset before the condition was enabled. To change this behavior, you can modify your condition as follows:

<CodeExample
  path="docs_snippets/docs_snippets/concepts/declarative_automation/on_missing/update_older_time_partitions_handled.py"
  title="src/<project_name>/defs/assets.py"
/>
