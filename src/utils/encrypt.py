import time
import random
import string
import hashlib
from src.utils.logger import LOGGER, ERROR_LOGGER

class ParameterEncryption:
    def __init__(self, data: dict, power_access_key="sJVgS8RdIBIBKbUD3dscfdez0iSrUhX1"):
        # 获取当前时间的毫秒时间戳
        self.millis = int(round(time.time() * 1000))
        LOGGER.info("Millis: %d" % self.millis)
        # 生成6位随机字符串
        self.random_string = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
        LOGGER.info("Random string: %s" % self.random_string)
        # power_access_key参数
        self.power_access_key = power_access_key
        LOGGER.info("Power access key: %s" % self.power_access_key)
        # 将字典的所有值拼接在一起，并对字典进行升序排序
        self.result = '&'.join([f'{key}={data[key]}' for key in sorted(data.keys())])
        LOGGER.info("Result: %s" % self.result)
        # self.result = '&'.join([f'{key}={data[key]}' for key in data.keys()])

    def power_sign(self):
        a = f'{self.result}&{self.millis}{self.random_string}{self.power_access_key}'
        LOGGER.info("Power sign: %s" % a)
        hashed_a = hashlib.md5(a.encode()).hexdigest()
        return hashed_a

    def ed_header(self):
        header = {
            'power-timestamp': str(self.millis),
            'power-nonce': self.random_string,
            'power-access-key': "d8BCEqCaaGC9FasF",
            'power-sign': self.power_sign()
        }
        return header
