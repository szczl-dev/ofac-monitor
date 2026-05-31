"""
变更检测模块
对比新旧 SDN 数据快照，检测新增、移除、修改的实体
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

from .db import Database

logger = logging.getLogger(__name__)


class ChangeDetector:
    """SDN 变更检测器"""

    def __init__(self, db: Database):
        self.db = db

    def detect(self, snapshot_date: str, prev_date: str,
               new_entries: List[Dict]) -> List[Dict]:
        """
        检测新旧快照之间的变化

        参数:
          snapshot_date: 新快照日期
          prev_date: 上一快照日期
          new_entries: 新解析的实体列表

        返回: 变更记录列表
        """
        logger.info(f"检测变更: {prev_date} → {snapshot_date}")

        # 获取上一版本的实体哈希
        old_hashes = self.db.get_entry_hashes(prev_date)

        # 构建新版本的 {uid: entry}
        new_map = {e["uid"]: e for e in new_entries}
        new_hashes = {uid: e["content_hash"] for uid, e in new_map.items()}

        old_uids = set(old_hashes.keys())
        new_uids = set(new_hashes.keys())

        # 检测变更
        added_uids = new_uids - old_uids
        removed_uids = old_uids - new_uids
        common_uids = new_uids & old_uids

        # 修改：uid 相同但哈希不同
        modified_uids = {
            uid for uid in common_uids
            if new_hashes[uid] != old_hashes[uid]
        }

        logger.info(
            f"变更检测结果: "
            f"新增 {len(added_uids)}, "
            f"移除 {len(removed_uids)}, "
            f"修改 {len(modified_uids)}"
        )

        # 构建变更记录
        changes = []

        # 新增
        for uid in added_uids:
            entry = new_map[uid]
            changes.append(self._make_change(
                snapshot_date, prev_date, "added", entry
            ))

        # 移除：需要从旧快照中获取详情
        if removed_uids:
            old_entries = self.db.get_entries_by_uids(list(removed_uids), prev_date)
            old_map = {e["uid"]: e for e in old_entries}
            for uid in removed_uids:
                entry = old_map.get(uid, {})
                changes.append(self._make_change(
                    snapshot_date, prev_date, "removed", entry
                ))

        # 修改
        for uid in modified_uids:
            entry = new_map[uid]
            old_entry = self.db.get_entries_by_uids([uid], prev_date)
            old_data = old_entry[0] if old_entry else {}

            detail = self._diff_entries(old_data, entry)
            changes.append(self._make_change(
                snapshot_date, prev_date, "modified", entry, detail
            ))

        return changes

    # ==================== 内部方法 ====================

    def _make_change(self, snapshot_date: str, prev_date: str,
                     change_type: str, entry: Dict,
                     detail: Optional[Dict] = None) -> Dict:
        """构建一条变更记录"""
        return {
            "snapshot_date": snapshot_date,
            "prev_date": prev_date,
            "change_type": change_type,
            "uid": entry.get("uid", 0),
            "last_name": entry.get("last_name", ""),
            "first_name": entry.get("first_name", ""),
            "sdn_type": entry.get("sdn_type", ""),
            "programs": json.dumps(entry.get("programs", []), ensure_ascii=False),
            "remarks": entry.get("remarks", ""),
            "detail": json.dumps(detail, ensure_ascii=False) if detail else "",
        }

    def _diff_entries(self, old: Dict, new: Dict) -> Dict:
        """比较两个实体，返回变更的字段"""
        diff = {}
        comparable_fields = [
            "last_name", "first_name", "sdn_type", "remarks"
        ]

        for field in comparable_fields:
            ov = (old.get(field) or "").strip()
            nv = (new.get(field) or "").strip()
            if ov != nv:
                diff[field] = {"old": ov, "new": nv}

        # 比较制裁项目
        old_progs = set()
        if old.get("programs"):
            try:
                old_progs = set(json.loads(old["programs"])
                                if isinstance(old["programs"], str)
                                else old["programs"])
            except (json.JSONDecodeError, TypeError):
                pass

        new_progs = set(new.get("programs", []))
        if old_progs != new_progs:
            diff["programs"] = {
                "old": sorted(old_progs),
                "new": sorted(new_progs),
            }

        return diff
