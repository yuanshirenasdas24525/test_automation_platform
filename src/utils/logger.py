# coding: utf-8
# 日志配置字典
import logging.config
from config.settings import ProjectPaths

# 如果之后要实现热更，这里可以改为从 config_center 获取
# LOG_LEVEL = config_center.get('system', 'log_level', 'DEBUG')
LOG_LEVEL = 'DEBUG'

LOGGING_DIC = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '%(asctime)s.%(msecs)03d %(threadName)s [%(name)s] %(levelname)s [%(pathname)s:%(lineno)d] %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
        'simple': {
            'format': '%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s',
            'datefmt': '%H:%M:%S',
        },
    },
    'handlers': {
        # 1. 开发阶段在控制台看详细信息
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'simple'
        },
        # 2. 所有的业务流水日志（INFO及以上）
        'file_info': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': ProjectPaths.INFO_LOG, #
            'maxBytes': 1024 * 1024 * 10,
            'backupCount': 10,
            'encoding': 'utf-8',
            'formatter': 'standard',
        },
        # 3. 专门收集报错，方便排查崩溃问题
        'file_error': {
            'level': 'ERROR',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': ProjectPaths.ERROR_LOG, #
            'maxBytes': 1024 * 1024 * 5,
            'backupCount': 5,
            'encoding': 'utf-8',
            'formatter': 'standard',
        },
    },
    'loggers': {
        # 根记录器：捕获所有日志
        '': {
            'handlers': ['console', 'file_info', 'file_error'],
            'level': LOG_LEVEL,
            'propagate': True,
        },
    }
}

logging.config.dictConfig(LOGGING_DIC)
# 统一使用这一个实例即可，内部会自动分流
LOGGER = logging.getLogger('API_PLATFORM')