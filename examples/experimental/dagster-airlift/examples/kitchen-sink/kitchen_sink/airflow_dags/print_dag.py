from datetime import datetime
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator
from dagster_airlift.in_airflow import mark_as_dagster_migrating
from dagster_airlift.migration_state import load_migration_state_from_yaml


def print_hello():
    print("Hello")  # noqa: T201


default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "start_date": datetime(2023, 1, 1),
    "retries": 1,
}

dag = DAG(
    "print_dag", default_args=default_args, schedule_interval=None, is_paused_upon_creation=False
)
print_op = PythonOperator(task_id="print_task", python_callable=print_hello, dag=dag)
downstream_print_op = PythonOperator(
    task_id="downstream_print_task", python_callable=print_hello, dag=dag
)

mark_as_dagster_migrating(
    migration_state=load_migration_state_from_yaml(Path(__file__).parent / "migration_state"),
    global_vars=globals(),
)
