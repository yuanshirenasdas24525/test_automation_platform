from pathlib import Path
from config.settings import ProjectPaths

def ensure_report_dirs():
    """
    创建缓存/报告目录：{CACHE_FILE}/{NOW}/(report_data|report)
    """
    base = Path(ProjectPaths.CACHE_FILE) / ProjectPaths.NOW
    report_data = base / "report_data"
    report_dir = base / "report"
    report_data.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_data, report_dir