"""App 安装包记录。

让用户在前端「App 包管理」页面上传 .apk / .ipa 文件，平台保存到磁盘
（默认 data/app_packages/）+ 记录元信息到 app_packages 表。后续在
app_install / app_launch 这类 step 编辑时，可以直接从下拉框「选包」，
自动把 file_path 填进 step.config.app_path 或 capabilities['app']。

字段说明：
  - name              用户给的友好名（"主 App 测试包" / "v3.2-rc1"）
  - file_name         上传时的原始文件名（保留扩展名，前端显示用）
  - file_path         服务器磁盘路径（绝对或相对工程根都行，由 upload 接口决定）
  - platform          android / ios（用扩展名 + 用户填的字段双重确认）
  - app_package       Android 安装后的 package id（从 aapt 解析或用户手填）
  - bundle_id         iOS 安装后的 bundle id
  - version           包版本号（versionName / CFBundleShortVersionString）
  - file_size         字节
  - project_id        可选：归属某个项目；NULL 表示全局可见
  - upload_time       上传时间
  - description       备注
"""
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, BigInteger, func

from database.base import Base


class AppPackage(Base):
    __tablename__ = "app_packages"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String(128), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_path = Column(String(512), nullable=False)
    platform = Column(String(20), nullable=False)  # android | ios
    app_package = Column(String(255))  # Android: com.example.app
    bundle_id = Column(String(255))    # iOS: com.example.App
    version = Column(String(64))
    file_size = Column(BigInteger, nullable=False, default=0)

    # 可选归属
    project_id = Column(
        Integer, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True,
    )

    description = Column(String(255))
    upload_time = Column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return (
            f"<AppPackage id={self.id} name={self.name!r} platform={self.platform} "
            f"file={self.file_name}>"
        )
