"""
XML 解析模块
解析 OFAC SDN XML 文件，提取制裁实体信息
"""

import hashlib
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# SDN XML 命名空间
NS = {
    "": "",  # 默认命名空间
}


def _strip_ns(tag: str) -> str:
    """去除 XML 命名空间前缀"""
    return tag.split("}", 1)[1] if "}" in tag else tag


class SdnParser:
    """SDN XML 解析器"""

    def parse(self, xml_content: str) -> Tuple[Dict, List[Dict]]:
        """
        解析 SDN XML

        返回: (publish_info, entries)
          - publish_info: {date, record_count}
          - entries: [{uid, last_name, first_name, sdn_type, programs, addresses, remarks, content_hash}]
        """
        logger.info("开始解析 SDN XML...")

        # 清理可能的 BOM
        if xml_content.startswith("﻿"):
            xml_content = xml_content[1:]

        root = ET.fromstring(xml_content)

        # 解析发布信息
        publish_info = self._parse_publish_info(root)

        # 解析实体列表
        entries = self._parse_entries(root)

        logger.info(
            f"解析完成: {publish_info['record_count']} 条记录 "
            f"(发布日期: {publish_info['publish_date']})"
        )

        return publish_info, entries

    # ==================== 内部方法 ====================

    def _parse_publish_info(self, root: ET.Element) -> Dict:
        """解析发布信息"""
        info = {
            "publish_date": "",
            "record_count": 0,
        }

        pub_elem = root.find(".//PublshInformation") or root.find(".//publshInformation")
        if pub_elem is not None:
            date_elem = pub_elem.find("Publish_Date")
            count_elem = pub_elem.find("Record_Count")

            if date_elem is not None and date_elem.text:
                info["publish_date"] = date_elem.text.strip()
            if count_elem is not None and count_elem.text:
                try:
                    info["record_count"] = int(count_elem.text.strip())
                except ValueError:
                    pass

        return info

    def _parse_entries(self, root: ET.Element) -> List[Dict]:
        """解析所有 SDN 实体"""
        entries = []
        sdn_entries = root.findall(".//sdnEntry")

        for i, entry_elem in enumerate(sdn_entries):
            try:
                entry = self._parse_single_entry(entry_elem)
                if entry:
                    entries.append(entry)
            except Exception as e:
                uid_elem = entry_elem.find("uid")
                uid = uid_elem.text if uid_elem is not None else "?"
                logger.warning(f"解析实体 #{i} (uid={uid}) 出错: {e}")
                continue

            # 进度日志
            if (i + 1) % 1000 == 0:
                logger.info(f"  已解析 {i + 1}/{len(sdn_entries)} 条记录...")

        return entries

    def _parse_single_entry(self, elem: ET.Element) -> Dict:
        """解析单个 SDN 实体"""
        uid = int(self._get_text(elem, "uid") or "0")

        # 名称
        last_name = self._get_text(elem, "lastName") or ""
        first_name = self._get_text(elem, "firstName") or ""
        sdn_type = self._get_text(elem, "sdnType") or ""

        # 制裁项目列表
        programs = self._parse_programs(elem)

        # 地址列表
        addresses = self._parse_addresses(elem)

        # 备注
        remarks = self._get_text(elem, "remarks") or ""

        # 计算本条记录的内容哈希
        content_for_hash = f"{uid}|{last_name}|{first_name}|{sdn_type}|{';'.join(sorted(programs))}|{remarks}"
        content_hash = hashlib.md5(content_for_hash.encode("utf-8")).hexdigest()

        return {
            "uid": uid,
            "last_name": last_name,
            "first_name": first_name,
            "sdn_type": sdn_type,
            "programs": programs,
            "addresses": addresses,
            "remarks": remarks,
            "content_hash": content_hash,
        }

    @staticmethod
    def _get_text(elem: ET.Element, tag: str) -> str:
        """获取子元素的文本内容"""
        child = elem.find(tag)
        return child.text.strip() if child is not None and child.text else ""

    def _parse_programs(self, elem: ET.Element) -> List[str]:
        """解析制裁项目列表"""
        programs = []
        prog_list = elem.find("programList")
        if prog_list is not None:
            for prog in prog_list.findall("program"):
                if prog.text and prog.text.strip():
                    programs.append(prog.text.strip())
        return programs

    def _parse_addresses(self, elem: ET.Element) -> List[Dict]:
        """解析地址列表"""
        addresses = []
        addr_list = elem.find("addressList")
        if addr_list is not None:
            for addr in addr_list.findall("address"):
                addr_dict = {}
                for field in ["uid", "address1", "address2", "address3",
                              "city", "stateOrProvince", "postalCode", "country"]:
                    val = self._get_text(addr, field)
                    if val:
                        addr_dict[field] = val
                if addr_dict:
                    addresses.append(addr_dict)
        return addresses
