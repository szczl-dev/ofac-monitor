"""
OpenSanctions 数据解析模块
解析 delta JSONL 格式的变更数据
"""

import json
import logging
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


class OpenSanctionsParser:
    """OpenSanctions delta 数据解析器"""

    # 需要关注的实体类型（这些是制裁目标的主要类型）
    TARGET_SCHEMAS = {
        "Person", "Organization", "Company", "Vessel", "Aircraft",
    }

    def parse_delta_changes(self, delta_entries: List[Dict],
                            check_date: str,
                            version: str) -> Tuple[List[Dict], Dict]:
        """
        解析 delta 条目为结构化的变更记录

        参数:
          delta_entries: 从 entities.delta.json 解析的变更列表
          check_date: 检查日期
          version: 数据集版本

        返回:
          (changes_for_db, stats)
          - changes_for_db: 可供数据库保存的结构化记录
          - stats: 变更统计 {total, added, modified, removed, targets, by_schema, by_program}
        """
        changes_for_db = []

        # 统计
        stats = {
            "total": 0,
            "added": 0,
            "modified": 0,
            "removed": 0,
            "targets": 0,
            "by_schema": {},
            "by_program": {},
            "by_country": {},
        }

        for entry in delta_entries:
            op = entry.get("op", "UNKNOWN")
            entity = entry.get("entity", {})

            entity_id = entity.get("id", "")
            entity_name = entity.get("caption", "")
            entity_schema = entity.get("schema", "Unknown")

            # 是制裁目标吗？
            is_target = 1 if entity.get("target") else 0

            # 提取属性和程序
            props = entity.get("properties", {})
            programs = self._extract_programs(props)
            countries = self._extract_countries(props)

            # 构造数据库记录
            change_record = (
                check_date,
                version,
                op,
                entity_id,
                entity_name,
                entity_schema,
                json.dumps(programs, ensure_ascii=False) if programs else "",
                json.dumps(countries, ensure_ascii=False) if countries else "",
                is_target,
                json.dumps(entry, ensure_ascii=False),
            )
            changes_for_db.append(change_record)

            # 更新统计
            stats["total"] += 1
            if op == "ADD":
                stats["added"] += 1
            elif op == "MOD":
                stats["modified"] += 1
            elif op == "DEL":
                stats["removed"] += 1

            if is_target:
                stats["targets"] += 1

            # 按类型统计
            stats["by_schema"][entity_schema] = \
                stats["by_schema"].get(entity_schema, 0) + 1

            # 按制裁项目统计
            for prog in programs:
                stats["by_program"][prog] = \
                    stats["by_program"].get(prog, 0) + 1

            # 按国家统计
            for country in countries:
                stats["by_country"][country] = \
                    stats["by_country"].get(country, 0) + 1

        logger.info(
            f"Delta 解析完成: 总计 {stats['total']} 条 "
            f"(新增 {stats['added']}, 修改 {stats['modified']}, "
            f"删除 {stats['removed']}, 制裁目标 {stats['targets']})"
        )

        return changes_for_db, stats

    # ==================== 内部方法 ====================

    @staticmethod
    def _extract_programs(props: Dict) -> List[str]:
        """从实体属性中提取制裁项目列表"""
        programs = props.get("programId", [])
        if isinstance(programs, str):
            programs = [programs]
        return sorted(set(programs))

    @staticmethod
    def _extract_countries(props: Dict) -> List[str]:
        """从实体属性中提取国家列表"""
        countries = set()
        # country 字段
        country = props.get("country", [])
        if isinstance(country, str):
            countries.add(country)
        elif isinstance(country, list):
            for c in country:
                if c:
                    countries.add(c)

        # nationality 字段
        nationality = props.get("nationality", [])
        if isinstance(nationality, str):
            countries.add(nationality)
        elif isinstance(nationality, list):
            for n in nationality:
                if n:
                    countries.add(n)

        return sorted(countries)
