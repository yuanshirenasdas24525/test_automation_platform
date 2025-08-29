from config.settings import ProjectPaths
from src.utils.platform_utils import clear_log_files

clear_log_files(ProjectPaths.INFO_LOG)
clear_log_files(ProjectPaths.ERROR_LOG)