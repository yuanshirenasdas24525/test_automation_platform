from celery_app import celery_app

@celery_app.task(name="tasks.run_test_task")
def run_test_task(t_id, r_id, cases, category):
    import pytest
    import json
    import os
    from src.database.db import DB
    from src.database.data_sync import sync_allure_to_db, finalize_report

    db_session = DB().session

    try:
        result_path = f"data/results/{t_id}"
        report_path = f"data/reports/{t_id}"

        pytest_args = [
            "-s", "-v",
            "-p", "config.pytest_config",
            "--report_id", str(r_id),
            "--category", category,
            "--alluredir", result_path,
            f"tests/service_run_executor.py::TestService::test_{str(category).lower()}_runner",
            f"--cases_data={json.dumps(cases)}"
        ]

        pytest.main(pytest_args)

        os.system(f"allure generate {result_path} -o {report_path} --clean")

        sync_allure_to_db(r_id, result_path, db_session)
        finalize_report(r_id, db_session, t_id)

    except Exception as e:
        print(f"任务执行失败: {e}")

    finally:
        db_session.close()