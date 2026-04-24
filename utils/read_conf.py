import configparser
from config.settings import ProjectPaths

class ReadConf:
    def __init__(self, file_path):
        self.config = configparser.ConfigParser()
        # 保持原始大小写
        self.config.optionxform = lambda option: option
        with open(file_path, 'r', encoding='utf-8') as fp:
            self.config.read_file(fp)

    def get_dict(self, section):
        return dict(self.config.items(section))

    def get_list(self, section, key):
        # 历史坑：空值 `key = ` 会被 split 成 [""]（单元素空串），
        # 调用方以为"空就不迭代"就炸了。这里统一把空串过滤掉。
        raw = self.config.get(section, key)
        if raw is None:
            return []
        return [x.strip() for x in raw.split(",") if x.strip()]

read_conf = ReadConf(ProjectPaths.OBJ_CONFIG)