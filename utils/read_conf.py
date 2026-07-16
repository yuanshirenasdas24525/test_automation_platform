import configparser
from config.settings import ProjectPaths


class ReadConf:
    """读取 ini 配置。

    历史上平台的 DB 连接兜底放在 config/object_conf.ini。现已改为**纯环境变量
    驱动**（见 database/db.py / alembic env.py），该文件被移除。为保证 import 期
    不因文件缺失而崩，这里对"文件不存在"做容忍处理：配置留空，各 get_* 返回空。
    """

    def __init__(self, file_path):
        self.config = configparser.ConfigParser()
        # 保持原始大小写
        self.config.optionxform = lambda option: option
        try:
            with open(file_path, 'r', encoding='utf-8') as fp:
                self.config.read_file(fp)
        except FileNotFoundError:
            # 文件已移除：DB 连接改由环境变量（.env）驱动，配置留空即可。
            pass

    def get_dict(self, section):
        if not self.config.has_section(section):
            return {}
        return dict(self.config.items(section))

    def get_list(self, section, key):
        # 历史坑：空值 `key = ` 会被 split 成 [""]（单元素空串），
        # 调用方以为"空就不迭代"就炸了。这里统一把空串过滤掉。
        if not self.config.has_option(section, key):
            return []
        raw = self.config.get(section, key)
        if raw is None:
            return []
        return [x.strip() for x in raw.split(",") if x.strip()]


read_conf = ReadConf(ProjectPaths.OBJ_CONFIG)
