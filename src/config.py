"""
配置管理模块
负责加载环境变量和应用配置
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Config:
    """应用配置类"""

    # 飞书 Webhook
    FEISHU_WEBHOOK_URL = os.getenv(
        "FEISHU_WEBHOOK_URL",
        ""
    )

    # 数据目录
    DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
    CURRENT_DIR = DATA_DIR / "current"
    ARCHIVE_DIR = DATA_DIR / "archive"

    # 数据库文件
    DB_PATH = DATA_DIR / "ofac_monitor.db"

    # 日志目录
    LOG_DIR = Path(os.getenv("LOG_DIR", BASE_DIR / "logs"))
    LOG_FILE = LOG_DIR / "monitor.log"

    # ==================== OpenSanctions API（主数据源，推荐） ====================
    # OpenSanctions 每日自动同步 OFAC 数据到全球 CDN
    # 提供预计算的 delta（变更）文件，无需下载完整数据即可检测变更
    OS_DATASET = "us_ofac_sdn"
    OS_INDEX_URL = f"https://data.opensanctions.org/datasets/latest/{OS_DATASET}/index.json"
    # delta.json 包含所有历史版本的 delta 文件 URL 索引
    OS_DELTA_INDEX_URL = "https://data.opensanctions.org/artifacts/{dataset}/{version}/delta.json"
    # entities.delta.json 是 JSONL 格式，每行一个变更操作
    OS_ENTITIES_DELTA_URL = "https://data.opensanctions.org/artifacts/{dataset}/{version}/entities.delta.json"

    # ==================== OFAC 官方数据源（备用） ====================
    SDN_XML_URL = "https://sanctionslistservice.ofac.treas.gov/api/publicationpreview/exports/sdn.xml"
    RECENT_ACTIONS_URL = "https://ofac.treasury.gov/sanctions-programs-and-country-information/recent-actions"

    # 请求超时设置（秒）
    REQUEST_TIMEOUT = 120
    DOWNLOAD_RETRIES = 3
    DOWNLOAD_RETRY_DELAY = 10  # 秒

    @classmethod
    def ensure_dirs(cls):
        """确保所有必要的目录存在"""
        for d in [cls.DATA_DIR, cls.CURRENT_DIR, cls.ARCHIVE_DIR, cls.LOG_DIR]:
            d.mkdir(parents=True, exist_ok=True)

    @classmethod
    def validate(cls):
        """验证必要的配置是否存在"""
        if not cls.FEISHU_WEBHOOK_URL:
            raise ValueError(
                "❌ 未配置 FEISHU_WEBHOOK_URL，请在 .env 文件中设置飞书 Webhook 地址"
            )
        print("✅ 配置验证通过")
        return True
