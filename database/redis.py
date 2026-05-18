import redis
from utils.logger import LOGGER
from utils.reload_config import config_center


def _get_redis_conf() -> dict:
    return config_center.get("redis", default={})


def redis_connect():
    d = _get_redis_conf()
    return redis.Redis(
        host=d.get("host", "127.0.0.1"),
        port=int(d.get("port", 6379)),
        db=int(d.get("db", 0)),
        password=d.get("password", ""),
        decode_responses=True
    )

def clear_cache(text: str):
    """
    清理 Redis 中的指定缓存
    """
    try:
        r = redis_connect()
        keys = r.keys(f"{text}")
        LOGGER.info(f"[Redis] 查询到缓存数据 {keys} ")
        for k in keys:
            r.delete(k)
        if keys:
            LOGGER.info(f"[Redis] 已清理 {len(keys)} 条 {text} 缓存")
        return True
    except Exception as e:
        LOGGER.error(f"[Redis] 清理缓存失败: {e}")
        return False

