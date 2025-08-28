import redis
from src.utils.logger import LOGGER
from src.utils.read_file import read_conf

d = read_conf.get_dict("forex_redis")

def redis_connect():
    return redis.Redis(
        host=d["host"],
        port=int(d["port"]),
        db=d["db"],
        password=d["password"],
        decode_responses=True
    )

def clear_captcha_cache():
    """
    清理 Redis 中的验证码缓存（captchaGen*）
    """
    try:
        r = redis_connect()
        keys = r.keys("captcha*")
        LOGGER.info(f"[Redis] 查询到缓存数据 {keys} ")
        for k in keys:
            r.delete(k)
        if keys:
            LOGGER.info(f"[Redis] 已清理 {len(keys)} 条 captchaGen* 缓存")
        return True
    except Exception as e:
        LOGGER.error(f"[Redis] 清理缓存失败: {e}")
        return False

