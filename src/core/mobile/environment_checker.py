# -*- coding:utf-8 -*-
# Create on
import subprocess
from src.utils.platform_utils import run_command_safely
from config.settings import ProjectPaths
from src.utils.logger import LOGGER, ERROR_LOGGER


class EnvironmentChecker:
    def __init__(self, appium_config: dict, app_start_config: dict):
        self.appium_config = appium_config
        self.app_start_config = app_start_config

    def check_appium_service(self):
        """检查Appium服务是否正常启动"""
        try:
            response = subprocess.check_output(['curl', '-s', self.appium_config.get("appium_service", "")], shell=True)
            if "status" in response.decode('utf-8'):
                LOGGER.info(f"Appium服务正常：{response.decode('utf-8')}")
                return True
            else:
                ERROR_LOGGER.info(f"Appium未启动：{response.decode('utf-8')}")
                return False
        except subprocess.CalledProcessError:
            return False

    def start_appium_service(self):
        """尝试本地启动Appium服务器"""
        port = int(self.appium_config.get("appium_service", "").split(":")[2])
        try:
            LOGGER.info("正在尝试本地启动Appium服务器")
            subprocess.run([f"appium -p {port}"], shell=True, start_new_session=True)
            LOGGER.info(f"Appium服务器启动成功")
        except Exception as e:
            ERROR_LOGGER.error(f"启动Appium服务器失败，错误信息{e}")


    def check_device_connection(self):
        """检查设备是否正常连接或启动"""
        try:
            result = subprocess.run(['adb', 'devices'], capture_output=True, text=True, shell=True)
            lines = result.stdout.strip().split('\n')
            device_lines = lines[1:]  # 获取除第一行之外的所有行
            connected_devices = [line.split()[0] for line in device_lines if
                                 len(line.split()) >= 2 and line.split()[1] == 'device']
            return connected_devices
        except subprocess.CalledProcessError:
            return False

    def try_connect_device(self):
        """尝试连接设备"""
        device_name = self.app_start_config.get("devicename", "")
        try:
            LOGGER.info(f"正在尝试连接设备: {device_name}")
            response = subprocess.run(["adb", "connect", device_name])
            LOGGER.info(f"设备连接成功: {response}")
        except Exception as e:
            LOGGER.error(f"设备连接失败: {e}")


    def check_app_installed(self):
        """检查需要执行的自动化app是否安装"""
        device = self.app_start_config.get("devicename", "")
        app_package = self.app_start_config.get("apppackage", "")
        if self.app_start_config.get("apppackage",""):
            result = subprocess.run(
                ['adb', '-s', device, 'shell', 'pm', 'list', 'packages'],
                capture_output=True,
                text=True)
            LOGGER.info(f"Android测试安装包正常")
            return app_package in result.stdout
        else:
            LOGGER.info(f"Ios设备，")


    def check_environment_and_device(self):
        def check_and_log(check_function):
            """
            检查指定的条件并记录日志。如果检查失败，则尝试执行备用操作。
            """
            if not check_function():
                LOGGER.info("连接到Appium服务器失败，尝试执行备用操作")
                self.start_appium_service()
                return False
            return True

        # 检查Appium服务器是否已启动
        check_and_log(self.check_appium_service)

        # 检查设备连接
        device_type = self.app_start_config["platformname"]
        if device_type == 'android':
            LOGGER.info("Android设备")
            driver_list = self.check_device_connection()
            if len(driver_list):
                LOGGER.info(f"当前Android设备名称：{driver_list}")
            else:
                LOGGER.info("没有设备连接，尝试连接设备")
                self.try_connect_device()  # 尝试连接设备
        elif device_type == 'ios':
            LOGGER.info("iOS设备")
            pass

if __name__ == '__main__':
    from src.utils.read_file import read_conf

    appium_service_url =read_conf.get_dict("appium_config")
    dn = read_conf.get_dict("app_start_config")
    print(dn.get("apppackage"))
    # EnvironmentChecker(appium_config=appium_service_url,app_start_config=dn).check_environment_and_device()
    # EnvironmentChecker(appium_config=appium_service_url, app_start_config=dn).try_connect_device()

