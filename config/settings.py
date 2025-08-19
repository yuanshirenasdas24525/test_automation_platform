from pathlib import Path
from datetime import datetime

class ProjectPaths:
    # 项目根目录
    BASE_DIR = Path(__file__).resolve().parent.parent

    # 时间戳
    NOW = datetime.now().strftime('%Y%m%d%H%M%S')

    # 配置文件
    CONF_DIR = BASE_DIR / "config"
    OBJ_CONFIG = CONF_DIR / "object_conf.ini"
    START_CONFIG = CONF_DIR / "app_start_settings.json"

    # 数据文件
    DATA_DIR = BASE_DIR / "data"
    REPORT_DIR = DATA_DIR / "reports"
    IMG_DIR = DATA_DIR / "images"
    VIDEO_DIR = DATA_DIR / "video"
    UPLOAD_DIR = DATA_DIR / "upload_file"
    CACHE_FILE = DATA_DIR / "cache_file"
    IMG_FILE = DATA_DIR / "images"

    # UI 自动化用例
    APP_UI_DIR = DATA_DIR / "app_ui"
    UU_PRO_DIR = APP_UI_DIR / "uu"
    H5_UU_PRO_DIR = APP_UI_DIR / "uu_h5"
    JUANCASH_PRO_DIR = APP_UI_DIR / "juancash"

    # API 自动化用例
    API_AUTO_DIR = DATA_DIR / "api_auto"
    UU_API = API_AUTO_DIR / "uu_api" / "uu_api.xlsx"


    # 日志
    LOG_DIR = DATA_DIR / "log"
    INFO_LOG = LOG_DIR / "info.log"
    ERROR_LOG = LOG_DIR / "error.log"
