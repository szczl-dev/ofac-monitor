"""
摘要生成模块
将检测到的 OFAC 制裁名单变更生成易读的 Markdown 摘要
"""

import json
from collections import Counter
from datetime import datetime
from typing import Dict, List

# 国家代码 → 名称映射（常用）
COUNTRY_NAMES = {
    "af": "阿富汗", "al": "阿尔巴尼亚", "dz": "阿尔及利亚", "ao": "安哥拉",
    "ar": "阿根廷", "am": "亚美尼亚", "au": "澳大利亚", "at": "奥地利",
    "az": "阿塞拜疆", "bh": "巴林", "bd": "孟加拉", "by": "白俄罗斯",
    "be": "比利时", "bo": "玻利维亚", "ba": "波黑", "br": "巴西",
    "bg": "保加利亚", "kh": "柬埔寨", "cm": "喀麦隆", "ca": "加拿大",
    "cl": "智利", "cn": "中国", "co": "哥伦比亚", "cr": "哥斯达黎加",
    "hr": "克罗地亚", "cu": "古巴", "cy": "塞浦路斯", "cz": "捷克",
    "dk": "丹麦", "do": "多米尼加", "ec": "厄瓜多尔", "eg": "埃及",
    "ee": "爱沙尼亚", "et": "埃塞俄比亚", "fi": "芬兰", "fr": "法国",
    "ge": "格鲁吉亚", "de": "德国", "gr": "希腊", "gt": "危地马拉",
    "hk": "香港", "hu": "匈牙利", "in": "印度", "id": "印度尼西亚",
    "ir": "伊朗", "iq": "伊拉克", "ie": "爱尔兰", "il": "以色列",
    "it": "意大利", "jp": "日本", "jo": "约旦", "kz": "哈萨克斯坦",
    "ke": "肯尼亚", "kp": "朝鲜", "kr": "韩国", "kw": "科威特",
    "kg": "吉尔吉斯斯坦", "la": "老挝", "lv": "拉脱维亚", "lb": "黎巴嫩",
    "ly": "利比亚", "lt": "立陶宛", "lu": "卢森堡", "mo": "澳门",
    "my": "马来西亚", "mx": "墨西哥", "md": "摩尔多瓦", "mn": "蒙古",
    "me": "黑山", "ma": "摩洛哥", "mm": "缅甸", "nl": "荷兰",
    "nz": "新西兰", "ng": "尼日利亚", "no": "挪威", "pk": "巴基斯坦",
    "pa": "巴拿马", "pe": "秘鲁", "ph": "菲律宾", "pl": "波兰",
    "pt": "葡萄牙", "qa": "卡塔尔", "ro": "罗马尼亚", "ru": "俄罗斯",
    "sa": "沙特阿拉伯", "rs": "塞尔维亚", "sg": "新加坡", "sk": "斯洛伐克",
    "si": "斯洛文尼亚", "za": "南非", "es": "西班牙", "lk": "斯里兰卡",
    "sd": "苏丹", "se": "瑞典", "ch": "瑞士", "sy": "叙利亚",
    "tw": "台湾", "tj": "塔吉克斯坦", "th": "泰国", "tr": "土耳其",
    "tm": "土库曼斯坦", "ua": "乌克兰", "ae": "阿联酋", "gb": "英国",
    "us": "美国", "uz": "乌兹别克斯坦", "ve": "委内瑞拉", "vn": "越南",
    "ye": "也门", "zw": "津巴布韦",
}

# 制裁项目名称映射
PROGRAM_NAMES = {
    "US-IRAN": "伊朗制裁",
    "US-GLOMAG": "全球马格尼茨基",
    "US-RUSHAR": "俄罗斯有害活动制裁",
    "US-NK": "朝鲜制裁",
    "US-SYRIA": "叙利亚制裁",
    "US-CUBA": "古巴制裁",
    "US-VENEZUELA": "委内瑞拉制裁",
    "US-TERR": "反恐制裁",
    "US-NARC": "毒品走私制裁",
    "US-CAATSA": "《制敌法案》制裁",
    "US-UKRAINE": "乌克兰相关制裁",
    "US-CYBER": "网络相关制裁",
    "US-BURMA": "缅甸制裁",
    "US-SUDAN": "苏丹制裁",
    "US-BELARUS": "白俄罗斯制裁",
    "SDGT": "全球恐怖分子",
    "SDNTK": "毒品走私头目",
    "NPWMD": "大规模杀伤性武器扩散",
    "FTO": "外国恐怖组织",
}


class Summarizer:
    """变更摘要生成器"""

    def generate_summary(self, changes: List[Dict],
                         index_info: Dict,
                         stats: Dict,
                         check_date: str) -> str:
        """
        从 delta 变更数据生成摘要

        参数:
          changes: 数据库变更记录列表
          index_info: OpenSanctions index.json 数据
          stats: 变更统计
          check_date: 检查日期
        """
        if stats["total"] == 0:
            return self._no_changes_summary(index_info, check_date)

        # 分类
        added = [c for c in changes if c["operation"] == "ADD" and c["is_target"]]
        removed = [c for c in changes if c["operation"] == "DEL" and c["is_target"]]
        modified = [c for c in changes if c["operation"] == "MOD" and c["is_target"]]
        non_target = [c for c in changes if not c["is_target"]]

        last_change = index_info.get("last_change", check_date)
        entity_count = index_info.get("entity_count", 0)
        target_count = index_info.get("target_count", 0)

        lines = []
        lines.append(f"## 📊 OFAC 制裁名单变更通知\n")
        lines.append(f"**数据版本**: {index_info.get('version', 'N/A')}")
        lines.append(f"**数据更新时间**: {last_change}")
        lines.append(f"**当前名单规模**: {entity_count:,} 个实体（其中 {target_count:,} 个制裁目标）\n")
        lines.append(f"---\n")
        lines.append(f"### 📈 本次变更概览\n")
        lines.append(f"| 变更类型 | 数量 | 其中制裁目标 |")
        lines.append(f"|---------|------|------------|")
        lines.append(f"| 🆕 新增 | **{stats['added']}** | {len(added)} |")
        lines.append(f"| ✏️ 修改 | **{stats['modified']}** | {len(modified)} |")
        lines.append(f"| ❌ 移除 | **{stats['removed']}** | {len(removed)} |")
        lines.append(f"| **合计** | **{stats['total']}** | **{stats['targets']}** |")
        if non_target:
            lines.append(f"\n> 💡 另有 {len(non_target)} 条非制裁目标实体的变更（如别名、地址等辅助信息）\n")

        # 新增制裁目标详情
        if added:
            lines.append(f"---\n")
            lines.append(f"### 🆕 新增制裁目标 ({len(added)})\n")
            lines.append(self._format_entity_table(added))

        # 移除制裁目标详情
        if removed:
            lines.append(f"---\n")
            lines.append(f"### ❌ 移除制裁目标 ({len(removed)})\n")
            lines.append(self._format_entity_table(removed))

        # 修改制裁目标详情
        if modified:
            lines.append(f"---\n")
            lines.append(f"### ✏️ 修改制裁目标 ({len(modified)})\n")
            lines.append(self._format_entity_table(modified, max_items=15))

        # 制裁项目分布
        by_program = stats.get("by_program", {})
        if by_program:
            lines.append(f"---\n")
            lines.append(f"### 🏷️ 涉及制裁项目\n")
            lines.append(f"| 制裁项目 | 涉及数量 |")
            lines.append(f"|---------|---------|")
            for prog, count in sorted(by_program.items(), key=lambda x: -x[1])[:10]:
                display = PROGRAM_NAMES.get(prog, prog)
                lines.append(f"| {display} | {count} |")

        # 涉及国家/地区
        by_country = stats.get("by_country", {})
        if by_country:
            lines.append(f"\n### 🌍 涉及国家/地区\n")
            country_items = []
            for cc, count in sorted(by_country.items(), key=lambda x: -x[1])[:10]:
                display = COUNTRY_NAMES.get(cc, cc.upper())
                country_items.append(f"`{display}`({count})")
            lines.append(" · ".join(country_items))

        # 按实体类型
        by_schema = stats.get("by_schema", {})
        if by_schema:
            lines.append(f"\n### 📋 按实体类型\n")
            lines.append(f"| 类型 | 数量 |")
            lines.append(f"|------|------|")
            for stype, count in sorted(by_schema.items(), key=lambda x: -x[1]):
                lines.append(f"| {stype} | {count} |")

        # 页脚
        lines.append(f"\n---\n")
        lines.append(f"📌 数据来源: [OpenSanctions / OFAC SDN](https://www.opensanctions.org/datasets/us_ofac_sdn/)")
        lines.append(f"⏰ 检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"🔗 OFAC 官方: [sanctionssearch.ofac.treas.gov](https://sanctionssearch.ofac.treas.gov/)")

        return "\n".join(lines)

    def generate_status_report(self, index_info: Dict,
                               check_date: str) -> str:
        """
        生成当前状态报告（首次运行或无变化时）
        """
        lines = []
        lines.append(f"## 📊 OFAC 制裁名单监控状态\n")
        lines.append(f"**数据版本**: {index_info.get('version', 'N/A')}")
        lines.append(f"**数据更新时间**: {index_info.get('last_change', check_date)}")
        lines.append(f"**当前实体总数**: {index_info.get('entity_count', 0):,}")
        lines.append(f"**制裁目标数**: {index_info.get('target_count', 0):,}\n")
        lines.append(f"---\n")
        lines.append(f"ℹ️ 这是首次运行/定期状态报告。\n")
        lines.append(f"系统已建立监控基线，后续检测到 OFAC 制裁名单变更时将自动推送详细通知。\n")
        lines.append(f"📌 数据来源: [OpenSanctions](https://www.opensanctions.org/datasets/us_ofac_sdn/)")
        lines.append(f"⏰ 检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        return "\n".join(lines)

    # ==================== 内部方法 ====================

    @staticmethod
    def _no_changes_summary(index_info: Dict, check_date: str) -> str:
        """无变更时的摘要"""
        lines = []
        lines.append(f"## 📊 OFAC 制裁名单监测\n")
        lines.append(f"**数据版本**: {index_info.get('version', 'N/A')}")
        lines.append(f"**数据更新时间**: {index_info.get('last_change', check_date)}")
        lines.append(f"**检测时间**: {check_date}\n")
        lines.append(f"✅ 本次检测未发现制裁名单变化。\n")
        lines.append(f"当前名单共 {index_info.get('target_count', 0):,} 个制裁目标。\n")
        lines.append(f"📌 数据来源: [OpenSanctions](https://www.opensanctions.org/datasets/us_ofac_sdn/)")
        return "\n".join(lines)

    @staticmethod
    def _format_entity_table(entities: List[Dict], max_items: int = 20) -> str:
        """格式化实体为表格"""
        lines = ["| # | 名称 | 类型 | 国家 | 制裁项目 |",
                 "|---|------|------|------|---------|"]
        shown = entities[:max_items]
        for i, e in enumerate(shown, 1):
            name = e.get("entity_name", "N/A")
            etype = e.get("entity_schema", "N/A")

            # 解析国家
            countries_str = ""
            if e.get("countries"):
                try:
                    countries = json.loads(e["countries"]) \
                        if isinstance(e["countries"], str) else e["countries"]
                    countries_str = ", ".join(
                        COUNTRY_NAMES.get(c, c.upper()) for c in countries[:3]
                    )
                except (json.JSONDecodeError, TypeError):
                    pass

            # 解析项目
            prog_str = ""
            if e.get("programs"):
                try:
                    progs = json.loads(e["programs"]) \
                        if isinstance(e["programs"], str) else e["programs"]
                    prog_str = ", ".join(
                        PROGRAM_NAMES.get(p, p) for p in progs[:2]
                    )
                except (json.JSONDecodeError, TypeError):
                    pass

            # 截断过长名称
            display_name = name[:60] + "..." if len(name) > 60 else name

            lines.append(f"| {i} | {display_name} | {etype} | {countries_str} | {prog_str} |")

        if len(entities) > max_items:
            lines.append(f"\n> ... 还有 {len(entities) - max_items} 条，完整列表见日志或数据库")

        return "\n".join(lines) + "\n"
