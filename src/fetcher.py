"""
数据获取模块
从 OpenSanctions CDN 获取 OFAC 制裁名单数据和变更信息
"""

import json
import logging
import time
from typing import Dict, List, Optional

import requests

from .config import Config

logger = logging.getLogger(__name__)


class Fetcher:
    """OFAC 数据下载器（优先使用 OpenSanctions CDN）"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "OFAC-Monitor/1.0 (Compliance Monitoring Tool)"
            ),
            "Accept": "application/json, */*",
        })

    # ==================== OpenSanctions API ====================

    def fetch_index(self) -> Dict:
        """
        获取 OpenSanctions 数据集索引（~4KB，极快）
        包含 last_change、entity_count、version 等元信息

        返回: index 字典
        """
        url = Config.OS_INDEX_URL
        logger.info(f"获取数据集索引: {url}")

        content = self._download_with_retry(url, "Index")
        return json.loads(content)

    def fetch_delta_index(self, version: str) -> Dict:
        """
        获取指定版本的 delta 索引
        包含该版本及历史版本的 delta 文件 URL 列表

        返回: delta 索引字典，含 versions 映射
        """
        url = Config.OS_DELTA_INDEX_URL.format(
            dataset=Config.OS_DATASET,
            version=version,
        )
        logger.info(f"获取 delta 索引: {url}")

        content = self._download_with_retry(url, "Delta Index")
        return json.loads(content)

    def fetch_entities_delta(self, version: str) -> List[Dict]:
        """
        获取指定版本的实体变更列表（JSONL 格式）
        每行是一个变更操作: {op: "ADD"/"MOD"/"DEL", entity: {...}}

        返回: 变更操作列表
        """
        url = Config.OS_ENTITIES_DELTA_URL.format(
            dataset=Config.OS_DATASET,
            version=version,
        )
        logger.info(f"获取实体变更: {url}")

        content = self._download_with_retry(url, "Entities Delta")

        # 解析 JSONL
        changes = []
        for i, line in enumerate(content.strip().split("\n")):
            if line.strip():
                try:
                    changes.append(json.loads(line))
                except json.JSONDecodeError as e:
                    logger.warning(f"解析 delta 第 {i+1} 行失败: {e}")
                    continue

        logger.info(f"解析到 {len(changes)} 条实体变更")
        return changes

    def fetch_next_delta(self, current_version: str,
                         target_version: str) -> List[Dict]:
        """
        获取从 current_version 到 target_version 之间的所有变更
        通过 delta 索引链式获取

        注意：OpenSanctions 的 delta 是相对于上一版本的增量，
        如果跳过了多个版本，需要逐个获取并合并。
        """
        all_changes = []

        # 获取 delta 索引
        delta_idx = self.fetch_delta_index(target_version)

        # delta 索引中的所有版本，按时间倒序排列
        versions = list(delta_idx.get("versions", {}).keys())

        # 当前版本在列表中 → 获取中间的 delta
        if current_version and current_version in versions:
            idx = versions.index(current_version)
            # 取 current_version 之前的所有版本
            needed = versions[:idx]  # 倒序，所以取前面的
            logger.info(f"需要获取 {len(needed)} 个中间版本的 delta")

            for ver in needed:
                try:
                    changes = self.fetch_entities_delta(ver)
                    all_changes.extend(changes)
                except Exception as e:
                    logger.warning(f"获取版本 {ver} 的 delta 失败: {e}")
                    continue
        else:
            # 首次运行或版本不匹配，直接获取当前版本的 delta
            logger.info("首次运行或版本跳跃，获取当前版本的 delta")
            all_changes = self.fetch_entities_delta(target_version)

        return all_changes

    # ==================== OFAC 官方（备用） ====================

    def check_ofac_update_time(self) -> Optional[str]:
        """
        检查 OFAC SDN XML 的最后修改时间
        快速检查，不下载文件
        """
        try:
            resp = self.session.head(
                Config.SDN_XML_URL,
                timeout=Config.REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            resp.raise_for_status()
            return resp.headers.get("last-modified")
        except Exception as e:
            logger.warning(f"检查 OFAC 更新时间失败: {e}")
            return None

    # ==================== 内部方法 ====================

    def _download_with_retry(self, url: str, label: str) -> str:
        """带重试的下载"""
        last_error = None

        for attempt in range(1, Config.DOWNLOAD_RETRIES + 1):
            try:
                logger.info(f"[{label}] 第 {attempt}/{Config.DOWNLOAD_RETRIES} 次请求...")
                resp = self.session.get(url, timeout=Config.REQUEST_TIMEOUT)
                resp.raise_for_status()

                content = resp.text
                logger.info(
                    f"[{label}] 下载成功，大小: {len(content):,} 字节 "
                    f"({len(content) / 1024:.1f} KB)"
                )
                return content

            except Exception as e:
                last_error = e
                logger.warning(f"[{label}] 第 {attempt} 次失败: {e}")
                if attempt < Config.DOWNLOAD_RETRIES:
                    wait = Config.DOWNLOAD_RETRY_DELAY * attempt
                    logger.info(f"等待 {wait} 秒后重试...")
                    time.sleep(wait)

        raise RuntimeError(
            f"[{label}] 下载失败，已重试 {Config.DOWNLOAD_RETRIES} 次: {last_error}"
        )
