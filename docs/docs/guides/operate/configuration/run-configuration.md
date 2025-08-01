---
title: 'Run configuration'
description: Dagster Job run configuration allows providing parameters to jobs at the time they're executed.
sidebar_position: 100
---

When you launch a job that materializes, executes, or instantiates a configurable entity, such as an asset, op, or resource, you can provide _run configuration_ for that entity. Within the function that defines the entity, you can access the passed-in configuration through the `config` parameter. Typically, the provided run configuration values correspond to a _configuration schema_ attached to the asset, op, or resource definition. Dagster validates the run configuration against the schema and proceeds only if validation is successful.

A common use of configuration is for a [schedule](/guides/automate/schedules) or [sensor](/guides/automate/sensors) to provide configuration to the job run it is launching. For example, a daily schedule might provide the day it's running on to one of the assets as a config value, and that asset might use that config value to decide what day's data to read.

:::info

The code examples in this guide conform to the project structure generated by the [`create-dagster project` CLI command](/api/dg/create-dagster#create-dagster-project). For more information, see [Creating a new Dagster project](/guides/build/projects/creating-a-new-project).

:::

## Defining configurable parameters for an asset, op, or job

You can specify configurable parameters accepted by an asset, op, or job by defining a config model subclass of <PyObject section="config" module="dagster" object="Config"/> and a `config` parameter to the corresponding asset or op function. These config models utilize [Pydantic](https://docs.pydantic.dev), a popular Python library for data validation and serialization.

During execution, the specified config is accessed within the body of the asset, op, or job with the `config` parameter.

<Tabs persistentKey="assetsorops">
<TabItem value="Using assets">

Here, we define a basic asset in `assets.py` and its configurable parameters in `resources.py`. `MyAssetConfig` is a subclass of <PyObject section="config" module="dagster" object="Config"/> that holds a single string value representing the name of a user. This config can be accessed through the `config` parameter in the asset body:

<CodeExample
  path="docs_snippets/docs_snippets/guides/operate/configuration/run_config/asset_example/assets.py"
  title="src/<project_name>/defs/assets.py"
  startAfter="start"
  endBefore="end"
/>

<CodeExample
  path="docs_snippets/docs_snippets/guides/operate/configuration/run_config/asset_example/resources.py"
  title="src/<project_name>/defs/resources.py"
/>

</TabItem>
<TabItem value="Using ops and jobs">

Here, we define a basic op in `ops.py` and its configurable parameters in `resources.py`. `MyOpConfig` is a subclass of <PyObject section="config" module="dagster" object="Config"/> that holds a single string value representing the name of a user. This config can be accessed through the `config` parameter in the asset body:

<CodeExample
  path="docs_snippets/docs_snippets/guides/operate/configuration/run_config/op_example/ops.py"
  title="src/<project_name>/defs/ops.py"
/>

<CodeExample
  path="docs_snippets/docs_snippets/guides/operate/configuration/run_config/op_example/resources.py"
  title="src/<project_name>/defs/resources.py"
/>

You can also build config into jobs.

</TabItem>
</Tabs>

These examples showcase the most basic config types that can be used. For more information on the set of config types Dagster supports, see [the advanced config types documentation](/guides/operate/configuration/advanced-config-types).

## Defining configurable parameters for a resource

Configurable parameters for a resource are defined by specifying attributes for a resource class, which subclasses <PyObject section="resources" module="dagster" object="ConfigurableResource"/>. The below resource defines a configurable connection URL, which can be accessed in any methods defined on the resource:

<CodeExample path="docs_snippets/docs_snippets/guides/operate/configuration/run_config/resource_example/resources.py" />

For more information on using resources, see the [External resources documentation](/guides/build/external-resources).

## Providing config values at runtime

To execute a job or materialize an asset that specifies config, you'll need to provide values for its parameters. How you provide these values depends on the interface you use: Python, the Dagster UI, or the command line (CLI).

<Tabs persistentKey="configtype">
<TabItem value="Python">

When specifying config from the Python API, you can use the `run_config` argument for <PyObject section="jobs" module="dagster" object="JobDefinition.execute_in_process" /> or <PyObject section="execution" module="dagster" object="materialize"/>. This takes a <PyObject section="config" module="dagster" object="RunConfig"/> object, within which we can supply config on a per-op or per-asset basis. The config is specified as a dictionary, with the keys corresponding to the op/asset names and the values corresponding to the config values.

<CodeExample
  path="docs_snippets/docs_snippets/guides/operate/configuration/run_config/providing_config_values/assets.py"
  title="src/<project_name>/defs/assets.py"
  startAfter="start"
  endBefore="end"
/>

</TabItem>
<TabItem value="Dagster UI">

From the UI's **Launchpad** tab, you can supply config as YAML using the config editor. Here, the YAML schema matches the layout of the defined config class. The editor has typeahead, schema validation, and schema documentation.

You can also click the **Scaffold Missing Config** button to generate dummy values based on the config schema. Note that a modal containing the Launchpad editor will pop up if you attempt to materialize an asset with a defined `config`.

![Config in the Dagster UI](/images/guides/operate/config-ui.png)

</TabItem>
<TabItem value="Command line">

When executing a job from Dagster's CLI with [`dg launch --job`](/api/dg/dg-cli#cmdoption-dg-launch-job), you can put config in a YAML file:

```YAML file=/concepts/configuration/good.yaml
ops:
  op_using_config:
    config:
      person_name: Alice
```

And then pass the file path with the `--config` option:

```bash
dg launch --job my_job --config my_config.yaml
```

</TabItem>
</Tabs>

## Using environment variables with config

Assets and ops can be configured using environment variables by passing an <PyObject section="resources" module="dagster" object="EnvVar" /> when constructing a config object. This is useful when the value is sensitive or may vary based on environment. If using Dagster+, environment variables can be [set up directly in the UI](/guides/operate/configuration/using-environment-variables-and-secrets).

<CodeExample
  path="docs_snippets/docs_snippets/guides/operate/configuration/run_config/using_env_vars/assets.py"
  title="src/<project_name>/defs/assets.py"
  startAfter="start"
  endBefore="end"
/>

For more information on using environment variables in Dagster, see [Using environment variables and secrets in Dagster code](/guides/operate/configuration/using-environment-variables-and-secrets).

## Validation

Dagster validates any provided run config against the corresponding Pydantic model. It will abort execution with a <PyObject section="errors" module="dagster" object="DagsterInvalidConfigError"/> or Pydantic `ValidationError` if validation fails. For example, both of the following will fail, because there is no `nonexistent_config_value` in the config schema:

<CodeExample
  path="docs_snippets/docs_snippets/guides/operate/configuration/run_config/validation/assets.py"
  title="src/<project_name>/defs/assets.py"
  startAfter="start"
  endBefore="end"
/>

## Next steps

Config is a powerful tool for making Dagster pipelines more flexible and observable. For a deeper dive into the supported config types, see the [advanced config types documentation](/guides/operate/configuration/advanced-config-types). For more information on using resources, which are a powerful way to encapsulate reusable logic, see the [resources documentation](/guides/build/external-resources).
