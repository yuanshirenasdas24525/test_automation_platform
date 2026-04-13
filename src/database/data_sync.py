# src/database/data_sync.py
import os
import json
from datetime import datetime
from sqlalchemy import func
from src.database.models import TestReport, TestStepReport


def sync_allure_to_db(report_id, result_path, db):
    try:
        pass_count, fail_count, error_count, skip_count = 0, 0, 0, 0

        # 遍历 result 目录
        for file in os.listdir(result_path):
            if file.endswith("-result.json"):
                with open(os.path.join(result_path, file), 'r', encoding='utf-8') as f:
                    res = json.load(f)

                    # 1. 统计状态
                    status = res.get("status")
                    if status == "passed":
                        pass_count += 1
                    elif status == "failed":
                        fail_count += 1
                    elif status == "broken":
                        error_count += 1
                    elif status == "skipped":
                        skip_count += 1

                    # 2. 写入 test_step_reports (适配你新设计的通用表结构)
                    step_record = TestStepReport(
                        report_id=report_id,
                        step_name=res.get("name"),
                        status=status,
                        duration=res.get("stop", 0) - res.get("start", 0) / 1000.0,  # 毫秒转秒
                        error_message=res.get("statusDetails", {}).get("message"),
                        # 从 Allure 标签中提取 target 或 action (如果有)
                        action=next((t['value'] for t in res.get('labels', []) if t['name'] == 'action'), None),
                        create_time=datetime.now()
                    )
                    db.add(step_record)

        # 3. 更新主报告状态
        main_report = db.query(TestReport).filter(TestReport.id == report_id).first()
        if main_report:
            main_report.pass_count = pass_count
            main_report.fail_count = fail_count
            main_report.error_count = error_count
            main_report.skip_count = skip_count
            main_report.total_count = pass_count + fail_count + error_count + skip_count
            main_report.status = "passed" if (fail_count + error_count) == 0 else "failed"
            main_report.end_time = datetime.now()

        db.commit()
    except Exception as e:
        print(f"Allure 数据回填失败: {e}")
        db.rollback()
    finally:
        db.close()



def finalize_report(report_id, db):


    try:
        # 1. 统计各状态的数量
        stats = db.query(
            TestStepReport.status,
            func.count(TestStepReport.id)
        ).filter(TestStepReport.report_id == report_id).group_by(TestStepReport.status).all()

        stat_dict = {status: count for status, count in stats}

        pass_count = stat_dict.get('passed', 0)
        fail_count = stat_dict.get('failed', 0)
        error_count = stat_dict.get('error', 0)
        skip_count = stat_dict.get('skipped', 0)
        total = pass_count + fail_count + error_count + skip_count

        # 2. 更新主报告表
        report = db.query(TestReport).filter(TestReport.id == report_id).first()
        if report:
            report.status = "passed" if (fail_count + error_count) == 0 else "failed"
            report.total_count = total
            report.pass_count = pass_count
            report.fail_count = fail_count
            report.error_count = error_count
            report.skip_count = skip_count
            report.end_time = datetime.now()
            report.duration = (report.end_time - report.start_time).total_seconds()

            db.commit()
    finally:
        db.close()