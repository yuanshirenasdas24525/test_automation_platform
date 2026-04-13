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
        return self.config.get(section, key).split(",")

read_conf = ReadConf(ProjectPaths.OBJ_CONFIG)