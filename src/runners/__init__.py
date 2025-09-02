from config.settings import ProjectPaths
from src.utils.platform_utils import clear_log_files, clear_directory

clear_log_files(ProjectPaths.LOG_DIR)
# clear_directory(ProjectPaths.CACHE_FILE)
# clear_directory(ProjectPaths.REPORT_DIR)