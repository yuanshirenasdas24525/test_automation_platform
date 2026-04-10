import redis
from src.utils.logger import LOGGER
from src.utils.read_test_cases import read_conf

d = read_conf.get_dict("redis")

def redis_connect():
    return redis.Redis(
        host=d["host"],
        port=int(d["port"]),
        db=d["db"],
        password=d["password"],
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

