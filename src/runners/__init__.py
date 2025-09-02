from config.settings import ProjectPaths
from src.utils.platform_utils import clear_log_files, delete_all_files_in_dir

clear_log_files(ProjectPaths.LOG_DIR)
# delete_all_files_in_dir(ProjectPaths.CACHE_FILE)
# delete_all_files_in_dir(ProjectPaths.REPORT_DIR)