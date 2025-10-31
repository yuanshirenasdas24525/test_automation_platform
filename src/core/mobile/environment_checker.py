# -*- coding:utf-8 -*-
import os.path
import subprocess
import re
from typing import List, Optional, Tuple
from src.utils.platform_utils import run_command_safely
from config.settings import ProjectPaths
from src.utils.logger import LOGGER, ERROR_LOGGER


class EnvironmentChecker:
    def __init__(self, appium_config: dict, app_start_config: dict):
        self.appium_config = appium_config
        self.app_start_config = app_start_config
        self._device_type = app_start_config.get("platformName", "android").lower()

    def check_appium_service(self) -> bool:
        """检查Appium服务是否正常启动"""
        appium_service_url = self.appium_config.get("appium_service", "")
        if not appium_service_url:
            ERROR_LOGGER.error("Appium服务URL未配置")
            return False

        try:
            # 使用更安全的命令执行方式
            result = run_command_safely(['curl', '-s', appium_service_url])
            if not result.success:
                return False

            response = result.stdout
            # 更健壮的检查逻辑
            if "status" in response or '"value":' in response:
                LOGGER.info(f"Appium服务正常：{appium_service_url}")
                return True
            else:
                ERROR_LOGGER.info(f"Appium服务异常响应：{response}")
                return False

        except Exception as e:
            ERROR_LOGGER.error(f"检查Appium服务时发生异常：{e}")
            return False

    def check_device_connection(self) -> Tuple[bool, List[str]]:
        """检查设备是否正常连接，返回连接状态和设备列表"""
        configured_device = self.app_start_config.get("devicename", "")

        try:
            result = run_command_safely(['adb', 'devices'])
            if not result.success:
                return False, []

            # 解析设备列表
            connected_devices = []
            for line in result.stdout.strip().split('\n')[1:]:  # 跳过第一行标题
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1] == 'device':
                    connected_devices.append(parts[0])

            if not connected_devices:
                LOGGER.warning("未找到任何连接的设备")
                return False, []

            # 检查配置的设备是否在连接列表中
            if configured_device and configured_device in connected_devices:
                LOGGER.info(f"设备正常在线：{configured_device}")
                return True, connected_devices
            elif configured_device:
                LOGGER.warning(f"配置的设备未连接。配置设备：{configured_device}，已连接设备：{connected_devices}")
                return False, connected_devices
            else:
                LOGGER.info(f"使用默认设备，已连接设备：{connected_devices}")
                return True, connected_devices

        except Exception as e:
            ERROR_LOGGER.error(f"检查设备连接时发生异常：{e}")
            return False, []

    def check_app_installed(self) -> bool:
        """检查应用是否安装"""
        device = self.app_start_config.get("devicename", "")
        app_package = self.app_start_config.get("apppackage", "")

        if not app_package:
            LOGGER.info("未配置应用包名，跳过应用安装检查")
            return True

        if self._device_type == 'ios':
            # iOS设备检查逻辑
            LOGGER.info("iOS设备应用安装检查暂未实现")
            return True
        else:
            # Android设备检查
            try:
                if not device:
                    # 如果没有指定设备，使用默认设备
                    cmd = ['adb', 'shell', 'pm', 'list', 'packages']
                else:
                    cmd = ['adb', '-s', device, 'shell', 'pm', 'list', 'packages']

                result = run_command_safely(cmd)
                if not result.success:
                    return False

                is_installed = app_package in result.stdout
                if is_installed:
                    LOGGER.info(f"应用已安装：{app_package}")
                else:
                    LOGGER.warning(f"应用未安装：{app_package}")
                return is_installed

            except Exception as e:
                ERROR_LOGGER.error(f"检查应用安装时发生异常：{e}")
                return False

    def check_appium_doctor(self) -> bool:
        """检查Appium环境配置"""
        try:
            result = run_command_safely(["appium-doctor"], shell=True)
            if result.success:
                LOGGER.info("Appium环境检查通过")
                # 可以进一步解析输出，检查关键项目
                if "necessary" in result.stdout and "All necessary" in result.stdout:
                    LOGGER.info("所有必要组件都已安装")
                return True
            else:
                ERROR_LOGGER.error(f"Appium环境检查失败：{result.stderr}")
                return False

        except Exception as e:
            ERROR_LOGGER.error(f"执行appium-doctor时发生异常：{e}")
            return False

    def start_appium_service(self) -> bool:
        """启动Appium服务"""
        appium_service_url = self.appium_config.get("appium_service", "")
        try:
            # 从URL中提取端口
            port_match = re.search(r':(\d+)$', appium_service_url)
            if not port_match:
                ERROR_LOGGER.error(f"无法从URL中解析端口：{appium_service_url}")
                return False

            port = port_match.group(1)
            appium_log_file = str(ProjectPaths.APPIUM_LOG)

            LOGGER.info(f"正在启动Appium服务，端口：{port}")
            # 使用subprocess.Popen而不是run，以便在后台运行
            process = subprocess.Popen(
                ["appium", "-p", port, "-g", appium_log_file],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            # 等待一段时间检查服务是否启动成功
            import time
            time.sleep(5)

            if process.poll() is None:  # 进程仍在运行
                LOGGER.info("Appium服务启动成功")
                return True
            else:
                stdout, stderr = process.communicate()
                ERROR_LOGGER.error(f"Appium服务启动失败：{stderr}")
                return False

        except Exception as e:
            ERROR_LOGGER.error(f"启动Appium服务时发生异常：{e}")
            return False

    def install_app_package(self) -> bool:
        """
        安装应用包

        Returns:
            bool: 安装成功返回True，否则返回False
        """
        package_path = self.appium_config.get("package_path", "")
        if not package_path:
            ERROR_LOGGER.error("未配置应用包路径")
            return False

        # 判断是全路径还是包名
        if os.path.isabs(package_path):
            # 已经是全路径
            full_package_path = package_path
            LOGGER.info(f"使用绝对路径安装应用: {full_package_path}")
        else:
            # 只有包名，需要拼接系统路径
            full_package_path = os.path.join(str(ProjectPaths.APP_PACKAGE), package_path)
            LOGGER.info(f"使用相对路径安装应用: {full_package_path}")

        # 检查应用包文件是否存在
        if not os.path.exists(full_package_path):
            ERROR_LOGGER.error(f"应用包文件不存在: {full_package_path}")
            return False

        # 执行安装命令
        LOGGER.info(f"开始安装应用: {full_package_path}")
        result = run_command_safely(["adb", "install", "-r", full_package_path])

        # 检查安装结果
        if result.success:
            LOGGER.info(f"应用安装成功: {package_path}")
            return True
        else:
            ERROR_LOGGER.error(f"应用安装失败: {package_path}, 错误: {result.stderr}")
            return False

    def try_connect_device(self) -> bool:
        """尝试连接设备"""
        device_name = self.app_start_config.get("devicename", "")

        try:
            if self._is_network_device(device_name):
                LOGGER.info(f"正在尝试连接网络设备: {device_name}")
                result = run_command_safely(["adb", "connect", device_name])
                if result.success and "connected" in result.stdout:
                    LOGGER.info(f"设备连接成功: {device_name}")
                    return True
                else:
                    ERROR_LOGGER.error(f"设备连接失败: {result.stderr}")
                    return False
            else:
                # 尝试启动模拟器
                return self._start_emulator()

        except Exception as e:
            ERROR_LOGGER.error(f"连接设备时发生异常: {e}")
            return False

    def _is_network_device(self, device_name: str) -> bool:
        """判断是否为网络设备（IP:端口格式）"""
        if not device_name:
            return False
        # 简单的IP:端口格式检查
        pattern = r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+$'
        return bool(re.match(pattern, device_name))

    def _start_emulator(self) -> bool:
        """启动模拟器"""
        try:
            result = run_command_safely(["emulator", "-list-avds"])
            if not result.success:
                ERROR_LOGGER.error("无法获取模拟器列表")
                return False

            avds = [avd.strip() for avd in result.stdout.split('\n') if avd.strip()]
            if not avds:
                ERROR_LOGGER.error("未找到可用的模拟器")
                return False

            # 使用第一个可用的模拟器
            emulator_name = avds[0]
            LOGGER.info(f"正在启动模拟器：{emulator_name}")

            # 在后台启动模拟器
            process = subprocess.Popen(
                ["emulator", "-avd", emulator_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            LOGGER.info(f"模拟器启动命令已执行: {emulator_name}")
            return True

        except Exception as e:
            ERROR_LOGGER.error(f"启动模拟器时发生异常: {e}")
            return False

    def check_environment_and_device(self) -> bool:
        """
        综合检查环境和设备状态，包括应用安装
        返回布尔值表示是否所有检查都通过
        """
        checks_passed = True

        # 1. 检查Appium服务
        LOGGER.info("=== 检查Appium服务 ===")
        if not self.check_appium_service():
            LOGGER.warning("Appium服务未运行，尝试启动...")
            if not self.start_appium_service():
                ERROR_LOGGER.error("Appium服务启动失败")
                checks_passed = False
            elif not self.check_appium_service():
                ERROR_LOGGER.error("Appium服务启动后仍然不可用")
                checks_passed = False

        # 2. 检查设备连接
        LOGGER.info("=== 检查设备连接 ===")
        device_connected, connected_devices = self.check_device_connection()
        if not device_connected:
            LOGGER.warning("设备未连接，尝试连接...")
            if not self.try_connect_device():
                ERROR_LOGGER.error("设备连接失败")
                checks_passed = False
            else:
                # 重新检查设备连接
                device_connected, connected_devices = self.check_device_connection()
                if not device_connected:
                    ERROR_LOGGER.error("设备连接后仍然不可用")
                    checks_passed = False

        # 3. 检查应用安装（仅当设备连接正常时）
        if device_connected:
            LOGGER.info("=== 检查应用安装 ===")
            if not self.check_app_installed():
                LOGGER.warning("应用未安装，尝试安装应用...")
                if self.install_app_package():
                    LOGGER.info("应用安装成功")
                    # 再次检查应用是否安装成功
                    if not self.check_app_installed():
                        ERROR_LOGGER.error("应用安装后仍然无法检测到")
                        checks_passed = False
                else:
                    LOGGER.error("应用安装失败")
                    # 根据需求决定是否将此视为致命错误
                    # checks_passed = False

        # 4. 可选：检查Appium环境配置
        LOGGER.info("=== 检查Appium环境配置 ===")
        if not self.check_appium_doctor():
            LOGGER.warning("Appium环境配置检查未通过，但将继续执行")

        # 总结检查结果
        if checks_passed:
            LOGGER.info("✅ 所有环境检查通过")
        else:
            ERROR_LOGGER.error("❌ 环境检查未通过，请检查上述错误信息")

        return checks_passed

if __name__ == '__main__':
    from src.utils.read_file import read_conf

    appium_service_url =read_conf.get_dict("appium_config")
    dn = read_conf.get_dict("app_start_config")
    EnvironmentChecker(appium_config=appium_service_url, app_start_config=dn).check_environment_and_device()

