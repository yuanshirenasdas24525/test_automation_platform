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
    REPORT_DIR = DATA_DIR / "report"
    IMG_DIR = DATA_DIR / "images"
    VIDEO_DIR = DATA_DIR / "video"
    UPLOAD_DIR = DATA_DIR / "upload_file"
    CACHE_FILE = DATA_DIR / "cache_file"
    IMG_FILE = DATA_DIR / "images"

    # 日志
    LOG_DIR = DATA_DIR / "log"
    INFO_LOG = LOG_DIR / "info.log"
    ERROR_LOG = LOG_DIR / "error.log"
    APPIUM_LOG = LOG_DIR / "appium.log"

    # 安装包
    APP_PACKAGE= DATA_DIR / "app_package"

# =========================================================
# 项目相关路径配置配置
# =========================================================

    # UI 自动化用例
    APP_UI_DIR = DATA_DIR / "app_ui"
    UU_PRO_DIR = APP_UI_DIR / "uu_ui"
    H5_UU_PRO_DIR = APP_UI_DIR / "uu_h5"
    ui_register_case = UU_PRO_DIR / "1_register.xlsx"

    # API 自动化用例
    API_AUTO_DIR = DATA_DIR / "api_auto"
    # 项目模块
    UU_API = API_AUTO_DIR / "uu_api" / "uu_api.xlsx"
    register = API_AUTO_DIR / "uu_api" / "register.xlsx"
    login = API_AUTO_DIR / "uu_api" / "login.xlsx"
    userinfo = API_AUTO_DIR / "uu_api" / "userinfo.xlsx"
    security = API_AUTO_DIR / "uu_api" / "security.xlsx"
    deposit = API_AUTO_DIR / "uu_api" / "deposit.xlsx"
    withdraw = API_AUTO_DIR / "uu_api" / "withdraw.xlsx"
    converter = API_AUTO_DIR / "uu_api" / "converter.xlsx"
    card = API_AUTO_DIR / "uu_api" / "card.xlsx"
    agent = API_AUTO_DIR / "uu_api" / "agent.xlsx"
    corporate = API_AUTO_DIR / "uu_api" / "corporate.xlsx"


