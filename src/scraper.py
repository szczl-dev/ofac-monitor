"""
OFAC 官方页面爬取模块
直接从 https://ofac.treasury.gov/recent-actions 获取每日更新的制裁行动数据

数据层次:
  1. 列表页 (/recent-actions): 包含最近的制裁行动条目列表
  2. 详情页 (/recent-actions/YYYYMMDD): 包含 SDN 名单变更详情和 Treasury 新闻稿链接
"""

import json
import logging
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .config import Config

logger = logging.getLogger(__name__)

# OFAC 页面基础 URL
OFAC_BASE = "https://ofac.treasury.gov"
RECENT_ACTIONS_URL = f"{OFAC_BASE}/recent-actions"

# 月份名称 → 数字映射
MONTH_MAP = {
    "January": "01", "February": "02", "March": "03", "April": "04",
    "May": "05", "June": "06", "July": "07", "August": "08",
    "September": "09", "October": "10", "November": "11", "December": "12",
}


class OFACScraper:
    """OFAC 官方页面爬取器"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36 "
                "OFAC-Monitor/2.0 (Compliance Monitoring Tool)"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        })

    # ==================== 列表页爬取 ====================

    def fetch_recent_actions(self, max_pages: int = 3) -> Tuple[List[Dict], bool]:
        """
        爬取 recent-actions 列表页，返回所有条目的结构化数据

        参数:
          max_pages: 最大翻页数（每页 10 条）

        返回: (actions, pagination_error)
          - actions: 行动条目列表
          - pagination_error: True 表示后续页抓取失败，结果不完整

        异常:
          第一页失败会直接抛出异常（调用方应终止任务）。
        """
        all_actions = []
        pagination_error = False

        for page in range(max_pages):
            url = RECENT_ACTIONS_URL
            if page > 0:
                url = f"{RECENT_ACTIONS_URL}?page={page}"

            logger.info(f"爬取列表页: {url}")
            try:
                html = self._fetch_html(url, f"Recent Actions 列表页 (第 {page + 1} 页)")
                actions = self._parse_listing_page(html)
                if not actions:
                    logger.info(f"第 {page + 1} 页无更多条目，停止翻页")
                    break
                all_actions.extend(actions)
                logger.info(f"  第 {page + 1} 页解析到 {len(actions)} 条行动")
            except Exception as e:
                if page == 0:
                    raise RuntimeError(
                        f"OFAC Recent Actions 首页抓取失败: {e}"
                    ) from e
                logger.error(
                    f"⚠️ 第 {page + 1} 页抓取失败: {e}。"
                    f"已返回前 {len(all_actions)} 条部分结果。"
                    f"可能存在漏报风险，下次运行将重试。"
                )
                pagination_error = True
                break

        if pagination_error:
            logger.warning(
                f"⚠️ 分页抓取不完整: 共爬取 {len(all_actions)} 条（部分结果）。"
                f"如果距上次运行间隔较长，后续页的更新可能遗漏，下次运行会补抓。"
            )

        logger.info(f"共爬取 {len(all_actions)} 条近期行动"
                    f"{' (部分结果，有分页错误)' if pagination_error else ''}")
        return all_actions, pagination_error

    def fetch_recent_actions_simple(self) -> List[Dict]:
        """
        只爬取第一页（最新 10 条），用于日常检查
        """
        logger.info("爬取最新近期行动（仅第一页）...")
        html = self._fetch_html(RECENT_ACTIONS_URL, "Recent Actions 首页")
        actions = self._parse_listing_page(html)
        logger.info(f"解析到 {len(actions)} 条行动")
        return actions

    # ==================== 详情页爬取 ====================

    def fetch_action_detail(self, action_url: str) -> Dict:
        """
        爬取单条行动的详情页

        参数:
          action_url: 相对路径，如 "/recent-actions/20260605"

        返回: 详情字典，包含:
          - date_mm_dd_yyyy: MM/DD/YYYY 格式
          - press_release_url: Treasury 新闻稿 URL
          - press_release_title: 新闻稿标题
          - body_html: 页面正文 HTML
          - body_text: 页面正文纯文本
          - sections: {section_title: content} 各段落内容
        """
        full_url = urljoin(OFAC_BASE, action_url)
        logger.info(f"爬取详情页: {full_url}")

        html = self._fetch_html(full_url, f"详情页 {action_url}")
        return self._parse_detail_page(html, action_url)

    # ==================== 内部: HTML 获取 ====================

    def _fetch_html(self, url: str, label: str) -> str:
        """带重试的 HTML 下载"""
        last_error = None

        for attempt in range(1, Config.DOWNLOAD_RETRIES + 1):
            try:
                logger.info(f"[{label}] 第 {attempt}/{Config.DOWNLOAD_RETRIES} 次请求...")
                resp = self.session.get(url, timeout=Config.REQUEST_TIMEOUT)
                resp.raise_for_status()

                html = resp.text
                logger.info(
                    f"[{label}] 下载成功，大小: {len(html):,} 字节 "
                    f"({len(html) / 1024:.1f} KB)"
                )
                return html

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

    # ==================== 内部: 列表页解析 ====================

    @staticmethod
    def _parse_listing_page(html: str) -> List[Dict]:
        """
        解析 recent-actions 列表页 HTML

        页面结构 (Drupal Views):
        <div class="margin-bottom-4 search-result views-row">
          <div>
            <div class="font-sans-lg ...">
              <a href="/recent-actions/YYYYMMDD[_NN]" hreflang="en">Title</a>
            </div>
          </div>
          <div>
            <div class="margin-top-1 font-sans-2xs ...">
              Month DD, YYYY - <a href="/recent-actions/category">Category</a>
            </div>
          </div>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        actions = []

        # 找到所有 views-row
        rows = soup.find_all("div", class_="views-row")
        if not rows:
            # 尝试 search-result 类
            rows = soup.find_all("div", class_=lambda c: c and "search-result" in c)

        for row in rows:
            try:
                # 提取标题和链接
                title_link = row.find("a", href=re.compile(r"/recent-actions/\d+"))
                if not title_link:
                    continue

                action_url = title_link.get("href", "").strip()
                title = title_link.get_text(strip=True)

                # 提取日期和分类
                date_div = row.find("div", class_=lambda c: c and "font-sans-2xs" in c)
                date_text = ""
                category = ""
                category_url = ""

                if date_div:
                    # 分别提取日期文本（<a> 之前的部分）和分类（<a> 标签）
                    # HTML 结构: "June 05, 2026 -   \n<a href="...">Category</a>"
                    cat_link = date_div.find("a")
                    if cat_link:
                        category = cat_link.get_text(strip=True)
                        category_url = cat_link.get("href", "").strip()

                    # 获取日期文本：移除 <a> 标签后的剩余文本
                    if cat_link:
                        cat_link.extract()  # 临时移除 <a>
                    date_text = date_div.get_text(strip=True)
                    if cat_link:
                        date_div.append(cat_link)  # 恢复 <a>

                    # 清理日期文本 "June 05, 2026 - " → "June 05, 2026"
                    date_text = re.sub(r'\s*-\s*$', '', date_text).strip()

                # 解析日期为 YYYY-MM-DD
                date_iso = _parse_date(date_text)

                action = {
                    "action_url": action_url,
                    "title": title,
                    "action_date": date_iso,
                    "date_raw": date_text,
                    "category": category,
                    "category_url": category_url,
                }
                actions.append(action)

            except Exception as e:
                logger.warning(f"解析列表条目失败: {e}")
                continue

        return actions

    # ==================== 内部: 详情页解析 ====================

    @staticmethod
    def _parse_detail_page(html: str, action_url: str) -> Dict:
        """
        解析详情页 HTML

        详情页结构 (Drupal Node - ofac_recent_action):
        - field--name-field-date: 日期 (MM/DD/YYYY)
        - field--name-field-press-release-link: Treasury 新闻稿链接
        - field--name-field-body: 正文 (包含 SDN 名单变更详情)
        """
        soup = BeautifulSoup(html, "html.parser")

        detail = {
            "action_url": action_url,
            "date_mm_dd_yyyy": "",
            "press_release_url": "",
            "press_release_title": "",
            "body_html": "",
            "body_text": "",
            "sections": {},
        }

        # 提取日期
        date_field = soup.find("div", class_="field--name-field-release-date")
        if not date_field:
            date_field = soup.find("div", class_="field--name-field-date")
        if date_field:
            date_item = date_field.find("div", class_="field__item")
            if date_item:
                detail["date_mm_dd_yyyy"] = date_item.get_text(strip=True)

        # 提取 Treasury 新闻稿链接
        pr_field = soup.find("div", class_="field--name-field-press-release-link")
        if pr_field:
            pr_link = pr_field.find("a")
            if pr_link:
                detail["press_release_url"] = pr_link.get("href", "").strip()
                detail["press_release_title"] = pr_link.get_text(strip=True)

        # 提取正文
        body_field = soup.find("div", class_="field--name-field-body")
        if body_field:
            body_item = body_field.find("div", class_="field__item")
            if body_item:
                detail["body_html"] = str(body_item)
                detail["body_text"] = body_item.get_text(separator="\n", strip=True)

                # 按 h3/h4 分段
                sections = {}
                current_section = "_intro"
                current_content = []

                for element in body_item.children:
                    if element.name in ("h3", "h4"):
                        # 保存上一段
                        if current_content:
                            text = " ".join(
                                e.get_text(strip=True)
                                for e in current_content
                                if hasattr(e, "get_text")
                            )
                            sections[current_section] = text.strip()

                        current_section = element.get_text(strip=True)
                        current_content = []
                    else:
                        if hasattr(element, "get_text") and element.get_text(strip=True):
                            current_content.append(element)

                # 最后一段
                if current_content or current_section not in sections:
                    text = " ".join(
                        e.get_text(strip=True)
                        for e in current_content
                        if hasattr(e, "get_text")
                    )
                    sections[current_section] = text.strip()

                detail["sections"] = sections

        return detail


def _parse_date(date_text: str) -> str:
    """
    解析 "Month DD, YYYY" 格式为 "YYYY-MM-DD"

    示例:
      "June 05, 2026" → "2026-06-05"
      "May 28, 2026"  → "2026-05-28"
    """
    if not date_text:
        return ""

    # 尝试直接匹配 "Month DD, YYYY"
    match = re.match(
        r"(\w+)\s+(\d{1,2}),?\s*(\d{4})",
        date_text
    )
    if match:
        month_name, day, year = match.groups()
        month = MONTH_MAP.get(month_name)
        if month:
            return f"{year}-{month}-{int(day):02d}"

    # 尝试 "MM/DD/YYYY"
    match = re.match(r"(\d{2})/(\d{2})/(\d{4})", date_text)
    if match:
        month, day, year = match.groups()
        return f"{year}-{month}-{day}"

    logger.warning(f"无法解析日期: {date_text!r}")
    return date_text


# =====================================================================
#  详情页结构化数据提取
# =====================================================================

# 常见国家关键词（用于从文本中提取国家）
_COUNTRY_KEYWORDS = [
    "Iran", "China", "Russia", "United Arab Emirates", "Turkey", "Marshall Islands",
    "Liberia", "Panama", "Hong Kong", "Singapore", "Switzerland", "United Kingdom",
    "India", "Pakistan", "Afghanistan", "Iraq", "Syria", "Lebanon", "Venezuela",
    "North Korea", "Cuba", "Belarus", "Myanmar", "Sudan", "South Africa", "Brazil",
    "Malaysia", "Germany", "France", "Italy", "Spain", "Netherlands", "Belgium",
    "Sweden", "Norway", "Denmark", "Finland", "Japan", "South Korea", "Taiwan",
    "Vietnam", "Thailand", "Indonesia", "Philippines", "Mexico", "Colombia",
    "Canada", "Australia", "New Zealand", "Austria", "Poland", "Czech",
    "Romania", "Bulgaria", "Greece", "Cyprus", "Malta", "Ireland",
    "Kazakhstan", "Uzbekistan", "Azerbaijan", "Georgia", "Armenia",
    "Serbia", "Croatia", "Slovenia", "Slovakia", "Hungary",
    "Lithuania", "Latvia", "Estonia", "Ukraine", "Moldova",
    "St. Kitts and Nevis", "Dominica", "Saint Vincent", "Belize",
    "Palau", "Malta", "Monaco", "Liechtenstein", "Luxembourg",
    "Qatar", "Kuwait", "Bahrain", "Oman", "Yemen", "Jordan",
    "Egypt", "Libya", "Tunisia", "Algeria", "Morocco",
    "Bangladesh", "Sri Lanka", "Nepal", "Cambodia", "Laos",
    "Mongolia", "Kenya", "Nigeria", "Ghana", "Ethiopia",
    "Tanzania", "Uganda", "Zimbabwe", "Zambia", "Mozambique",
    "Angola", "Namibia", "Botswana", "Mauritius", "Seychelles",
    "Congo", "Somalia", "Eritrea", "Djibouti", "Sudan",
    "Chad", "Niger", "Mali", "Senegal", "Guinea", "Liberia",
    "Sierra Leone", "Ivory Coast", "Burkina Faso", "Togo", "Benin",
    "Peru", "Chile", "Argentina", "Uruguay", "Paraguay", "Bolivia",
    "Ecuador", "Costa Rica", "Panama", "Guatemala", "Honduras",
    "El Salvador", "Nicaragua", "Jamaica", "Haiti", "Dominican Republic",
    "Bahamas", "Barbados", "Trinidad", "Guyana", "Suriname",
    "Cameroon", "Gabon", "Equatorial Guinea",
]


def parse_sanctioned_entities(detail: Dict) -> Dict:
    """
    从详情页 body_html 中解析出结构化的制裁实体数据

    利用 HTML 结构：
    - h3/h4 标签标记段落标题（如 "The following individuals have been added..."）
    - 同一类型的条目在同一个或相邻的 <p> 中，条目之间用 <br><br> 分隔

    返回:
      {
        "individuals": [{name, countries, programs, ...}, ...],
        "entities": [{name, countries, programs, linked_to}, ...],
        "vessels": [{name, flag, imo, programs, linked_to}, ...],
        "modifications": [description, ...],
        "admin_changes": str,
        "programs": {PROGRAM: count},
        "countries": {COUNTRY: count},
      }
    """
    body_html = detail.get("body_html", "")

    result = {
        "individuals": [],
        "entities": [],
        "vessels": [],
        "modifications": [],
        "admin_changes": "",
        "programs": {},
        "countries": {},
    }

    if not body_html:
        return result

    # 用 BeautifulSoup 重新解析 body_html，获取结构化段落
    soup = BeautifulSoup(body_html, "html.parser")

    # 收集所有标题和后续内容
    # 结构: <h3>SDN List Updates</h3><h4>individuals added...</h4><p>entries</p>
    #       <h4>entities added...</h4><p>entries</p>
    current_section = None
    section_entries = {
        "individuals": [],
        "entities": [],
        "vessels": [],
        "modifications": [],
        "admin": [],
    }

    for element in soup.find_all(["h3", "h4", "p", "hr"]):
        if element.name in ("h3", "h4"):
            text = element.get_text(strip=True)
            text_lower = text.lower()

            if "individual" in text_lower:
                current_section = "individuals"
            elif "entities" in text_lower or "entity" in text_lower:
                current_section = "entities"
            elif "vessel" in text_lower:
                current_section = "vessels"
            elif "change" in text_lower or "modification" in text_lower:
                current_section = "modifications"
            elif "administrative" in text_lower or "unrelated" in text_lower:
                current_section = "admin"
            else:
                current_section = None  # unknown section

        elif element.name == "p" and current_section:
            # 将 <p> 内容按 <br><br> 分割为独立条目
            html_str = str(element)
            entries = _split_html_entries(html_str)

            if current_section == "individuals":
                section_entries["individuals"].extend(entries)
            elif current_section == "entities":
                section_entries["entities"].extend(entries)
            elif current_section == "vessels":
                section_entries["vessels"].extend(entries)
            elif current_section == "modifications":
                section_entries["modifications"].extend(entries)
            elif current_section == "admin":
                section_entries["admin"].extend(entries)

        elif element.name == "hr":
            current_section = None

    # 解析每个条目
    result["individuals"] = [
        _parse_single_person(e) for e in section_entries["individuals"] if e.strip()
    ]
    result["entities"] = [
        _parse_single_org(e) for e in section_entries["entities"] if e.strip()
    ]
    result["vessels"] = [
        _parse_single_vessel(e) for e in section_entries["vessels"] if e.strip()
    ]

    # 修改项 — 逐个条目解析
    if section_entries["modifications"]:
        all_mods = []
        for mod_entry in section_entries["modifications"]:
            parsed = _parse_modifications(mod_entry)
            all_mods.extend(parsed)
        result["modifications"] = all_mods

    if section_entries["admin"]:
        result["admin_changes"] = " ".join(section_entries["admin"])

    # 汇总统计
    all_entities = result["individuals"] + result["entities"] + result["vessels"]
    for ent in all_entities:
        for prog in ent.get("programs", []):
            result["programs"][prog] = result["programs"].get(prog, 0) + 1
        for country in ent.get("countries", []):
            result["countries"][country] = result["countries"].get(country, 0) + 1

    return result


def _split_html_entries(html_str: str) -> List[str]:
    """
    从 <p> 的 HTML 中按 <br><br> 分割独立条目，返回纯文本列表
    """
    parts = re.split(r'<br\s*/?>\s*<br\s*/?>', html_str, flags=re.IGNORECASE)
    clean_parts = []
    for p_text in parts:
        # 清理 HTML 标签
        clean = re.sub(r'<[^>]+>', ' ', p_text)
        clean = re.sub(r'&nbsp;', ' ', clean)
        clean = re.sub(r'&amp;', '&', clean)    # HTML 实体解码
        clean = re.sub(r'&lt;', '<', clean)
        clean = re.sub(r'&gt;', '>', clean)
        clean = re.sub(r'&quot;', '"', clean)
        clean = re.sub(r'\s+', ' ', clean).strip()
        if clean and len(clean) > 10:
            clean_parts.append(clean)
    return clean_parts


def _parse_single_person(text: str) -> Dict:
    """解析单个个人条目"""
    text = text.strip()

    # 提取制裁项目标签 [PROGRAM]
    programs = re.findall(r'\[([A-Z][A-Z0-9\-]+(?:\s*-\s*[A-Z0-9]+)*)\]', text)
    clean_text = re.sub(r'\[[A-Z][A-Z0-9\-\s]*\]', '', text)  # 去掉标签便于解析

    # 提取名称：LAST, First 格式
    # OFAC 格式: "LAST, First Middle, City, Country; DOB ...; nationality ..."
    # 名称是第一个分号前的部分，去掉末尾的地点
    name = ""
    # 尝试用分号分割，取第一部分
    semicolon_parts = clean_text.split(";")
    first_part = semicolon_parts[0].strip()

    # 从第一部分中提取名称：去掉末尾的 ", City" 和 ", Country"
    # 格式: "LAST, First (a.k.a. ...), City, Country"
    # 简单策略：找到最后一个 "), " 或 ", " 的位置来分割名称和地点
    # 更稳健的做法：用已知国家列表匹配
    for country in _COUNTRY_KEYWORDS:
        # 匹配 ", Country" 模式
        pattern = f", {country}"
        idx = first_part.find(pattern)
        if idx > 0:
            first_part = first_part[:idx].strip().rstrip(",").strip()
            break

    # 如果还有 ", City" 残留（在国家之前），尝试去掉
    # e.g., "LAST, First, Tehran" → check if last comma-separated part looks like a city
    parts = first_part.split(", ")
    if len(parts) >= 3:
        # 检查最后一部分是否看起来像城市名（首字母大写，没有特殊字符）
        last_part = parts[-1]
        if last_part and last_part[0].isupper() and not any(c in last_part for c in "()\"'."):
            # 可能是城市名，去掉它
            first_part = ", ".join(parts[:-1])

    name = first_part.strip()

    if not name or len(name) < 3:
        name = clean_text[:80].strip()

    # 提取国籍
    countries = set()
    for m in re.finditer(r'nationality\s+([\w\s]+?)(?:;|\.|$)', clean_text):
        c = m.group(1).strip()
        if c and len(c) < 30 and not c.lower().startswith(('gender', 'additional', 'subject', 'passport', 'national')):
            countries.add(c)

    # 从位置中提取国家
    for country in _COUNTRY_KEYWORDS:
        if country in clean_text:
            countries.add(country)

    # 提取 aliases
    aliases = re.findall(r'\(a\.k\.a\.\s+([^)]+)\)', clean_text)

    # 提取位置
    location = ""
    loc_match = re.search(r',\s*((?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*)+(?:[A-Z][a-z]+))', clean_text)
    if loc_match:
        location = loc_match.group(1).strip()

    # 检测是否与中国/香港/澳门相关
    china_related = _is_china_related(countries, clean_text)

    return {
        "name": name.strip(",").strip(),
        "countries": sorted(countries),
        "programs": sorted(set(programs)),
        "aliases": [a.strip() for a in aliases],
        "location": location,
        "china_related": china_related,
    }


def _parse_single_org(text: str) -> Dict:
    """解析单个组织/实体条目"""
    text = text.strip()

    # 提取制裁项目标签
    programs = re.findall(r'\[([A-Z][A-Z0-9\-]+(?:\s*-\s*[A-Z0-9]+)*)\]', text)
    clean_text = re.sub(r'\[[A-Z][A-Z0-9\-\s]*\]', '', text)

    # 提取名称 — 第一个全大写部分直到遇到逗号+地址或分号
    # 实体名称可能包含 CO., LTD., INC., CORP. 等缩写
    name = ""
    name_match = re.match(
        r'([A-Z][A-Z\s\-\'&\.]{3,}'
        r'(?:\s*\(\s*a\.k\.a\.\s*[^)]+\))?'
        r'(?:\s*&\s*[A-Z][A-Z\s\-\'&\.]*)?'
        r'(?:,\s*(?:Ltd\.?|LTD\.?|LLC|Inc\.?|INC\.?|Corp\.?|CORP\.?|FZE|FZC|L\.L\.C\.?|Limited|LIMITED|Incorporated|INCORPORATED|Company|COMPANY))?'  # 公司后缀
        r')'
        r'(?:,|;|\s*\()',
        clean_text
    )
    if name_match:
        name = name_match.group(1).strip()
    else:
        name = clean_text[:80].strip()

    # 提取国家
    countries = set()
    for country in _COUNTRY_KEYWORDS:
        if country in clean_text:
            countries.add(country)

    # 提取 Linked To
    linked_to = ""
    lt_match = re.search(r'Linked\s+To:\s*([^\.\)]+)', text)
    if lt_match:
        linked_to = lt_match.group(1).strip().rstrip(".")

    # 检测是否与中国/香港/澳门相关
    china_related = _is_china_related(countries, clean_text)

    return {
        "name": name.strip(",").strip(),
        "countries": sorted(countries),
        "programs": sorted(set(programs)),
        "linked_to": linked_to,
        "china_related": china_related,
    }


def _parse_single_vessel(text: str) -> Dict:
    """解析单个船只条目"""
    text = text.strip()

    # 提取制裁项目标签
    programs = re.findall(r'\[([A-Z][A-Z0-9\-]+(?:\s*-\s*[A-Z0-9]+)*)\]', text)
    clean_text = re.sub(r'\[[A-Z][A-Z0-9\-\s]*\]', '', text)

    # 提取名称: VESSEL NAME 23 (CALLSIGN) 或 VESSEL NAME
    # 船名可包含数字（如 "MD 23"）
    name = ""
    name_match = re.match(r'([A-Z][A-Z0-9\s\-\']{2,}(?:\s*\([A-Z0-9]+\))?)', clean_text)
    if name_match:
        name = name_match.group(1).strip()
    else:
        name = clean_text[:60].strip()

    # 船类型
    vessel_type = ""
    type_match = re.search(
        r'(LPG\s*Tanker|Oil\s*Tanker|Crude\s*Oil\s*Tanker|Bulk\s*Carrier|'
        r'Container\s*Ship|General\s*Cargo|Vehicle\s*Carrier|Chemical\s*Tanker|'
        r'Product\s*Tanker|VLCC|Suezmax|Aframax)',
        text, re.IGNORECASE
    )
    if type_match:
        vessel_type = type_match.group(1).strip()

    # 船旗
    flag = ""
    flag_match = re.search(r'(?:Tanker|Ship|Carrier|Cargo)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)*)\s+flag', text)
    if flag_match:
        flag = flag_match.group(1).strip()

    # IMO
    imo = ""
    imo_match = re.search(r'IMO\s+(\d{7})', text)
    if imo_match:
        imo = imo_match.group(1)

    # Linked To
    linked_to = ""
    lt_match = re.search(r'Linked\s+To:\s*([^\.\)]+)', text)
    if lt_match:
        linked_to = lt_match.group(1).strip().rstrip(".")

    # 检测是否与中国相关（船公司、注册地等）
    china_related = _is_china_related(set(), clean_text)
    # 船旗如果是中国/香港也标记
    if flag in ("China", "Hong Kong", "Macau"):
        china_related = True

    return {
        "name": name.strip(",").strip(),
        "type": vessel_type,
        "flag": flag,
        "imo": imo,
        "programs": sorted(set(programs)),
        "linked_to": linked_to,
        "china_related": china_related,
    }


def _is_china_related(countries: set, text: str) -> bool:
    """检测实体是否与中国大陆/香港/澳门相关"""
    # 检查国家
    china_countries = {"China", "Hong Kong", "Macau"}
    if countries & china_countries:
        return True

    # 检查文本中的中国地名关键词
    china_keywords = [
        "Shanghai", "Beijing", "Shenzhen", "Guangzhou", "Guangdong",
        "Hong Kong", "Hongkong", "Macau", "Macao",
        "Tianjin", "Chengdu", "Wuhan", "Nanjing", "Hangzhou",
        "Xiamen", "Fuzhou", "Qingdao", "Dalian", "Ningbo",
        "Suzhou", "Wuxi", "Changsha", "Chongqing", "Xi'an",
        "Zhengzhou", "Harbin", "Shenyang", "Kunming", "Jinan",
        "Hefei", "Nanchang", "Changchun", "Taiyuan", "Shijiazhuang",
        "Urumqi", "Lanzhou", "Yinchuan", "Xining", "Lhasa",
        "Guangxi", "Yunnan", "Sichuan", "Hubei", "Hunan",
        "Jiangsu", "Zhejiang", "Fujian", "Shandong", "Henan",
        "Hainan", "Xinjiang", "Tibet", "Inner Mongolia",
        "Macau", "Macao", "Taipa", "Cotai",
        "China", "Chinese",
    ]
    text_lower = text.lower()
    for kw in china_keywords:
        if kw.lower() in text_lower:
            return True

    # 检查 Chinese Simplified 标签（OFAC 常见格式）
    if "chinese simplified" in text_lower or "chinese traditional" in text_lower:
        return True

    return False


def _parse_modifications(text: str) -> List[str]:
    """解析名单修改条目"""
    mods = []
    # 按 " -to- " 分割修改
    parts = re.split(r'\s*-to-\s*', text)
    if len(parts) >= 2:
        for i in range(0, len(parts) - 1, 2):
            old_part = parts[i].strip()
            new_part = parts[i + 1].strip() if i + 1 < len(parts) else ""

            # 提取名称
            name_match = re.match(r'([A-Z][A-Z\s\-\'"]{3,}(?:\s*\(a\.k\.a\.[^)]+\))?)', old_part)
            name = name_match.group(1).strip() if name_match else old_part[:60].strip()

            # 比较新旧 program 标签
            old_progs = set(re.findall(r'\[([A-Z0-9\-]+)\]', old_part))
            new_progs = set(re.findall(r'\[([A-Z0-9\-]+)\]', new_part))
            added_progs = new_progs - old_progs
            removed_progs = old_progs - new_progs

            desc_parts = [name]
            if added_progs:
                desc_parts.append(f"新增标签: {', '.join(sorted(added_progs))}")
            if removed_progs:
                desc_parts.append(f"移除标签: {', '.join(sorted(removed_progs))}")
            if not added_progs and not removed_progs:
                desc_parts.append("内容修改")

            mods.append(" — ".join(desc_parts))

    if not mods:
        # 没有 -to- 分隔符，整体作为一个修改
        text_clean = text.strip()
        if text_clean and text_clean.lower() != "none":
            mods.append(text_clean[:200])

    return mods


# =====================================================================
#  中文摘要生成
# =====================================================================

def summarize_detail_for_push(detail: Dict, max_length: int = 3000) -> str:
    """
    将详情页解析结果生成为中文 Markdown 摘要（用于飞书推送）

    返回中文格式的摘要，包含：
      - 制裁项目统计
      - 新增个人/实体/船只列表（名称不翻译）
      - 涉及国家和制裁项目
    """
    parsed = parse_sanctioned_entities(detail)
    lines = []

    # ---------- 总体概览 ----------
    individuals = parsed["individuals"]
    entities = parsed["entities"]
    vessels = parsed["vessels"]
    modifications = parsed["modifications"]
    programs = parsed["programs"]
    countries = parsed["countries"]

    stats = []
    if individuals:
        stats.append(f"👤 个人: {len(individuals)} 人")
    if entities:
        stats.append(f"🏢 实体/组织: {len(entities)} 个")
    if vessels:
        stats.append(f"🚢 船只: {len(vessels)} 艘")
    if modifications:
        stats.append(f"✏️ 名单修改: {len(modifications)} 项")

    if stats:
        lines.append("**📊 本次制裁概览:**")
        lines.append(" · ".join(stats))
        lines.append("")

    # ---------- 制裁项目及国家 ----------
    if programs:
        prog_names = {
            "IRAN-EO13902": "伊朗 EO13902",
            "IRAN-EO13846": "伊朗 EO13846",
            "IRAN-EO13599": "伊朗 EO13599",
            "IRAN": "伊朗制裁",
            "SDGT": "全球恐怖主义",
            "FTO": "外国恐怖组织",
            "ILLICIT-DRUGS-EO14059": "毒品走私 EO14059",
            "RUSSIA-EO14024": "俄罗斯 EO14024",
            "DPRK": "朝鲜制裁",
            "SYRIA": "叙利亚制裁",
            "VENEZUELA": "委内瑞拉制裁",
            "CAATSA": "《制敌法案》",
            "CYBER": "网络制裁",
            "GLOMAG": "全球马格尼茨基",
            "BELARUS": "白俄罗斯制裁",
            "BURMA": "缅甸制裁",
            "SUDAN": "苏丹制裁",
        }
        prog_labels = []
        for prog, count in sorted(programs.items(), key=lambda x: -x[1]):
            label = prog_names.get(prog, prog)
            prog_labels.append(f"{label}({count})")
        lines.append(f"**🏷️ 制裁项目:** {' · '.join(prog_labels)}")

    if countries:
        country_names = {
            "Iran": "伊朗", "China": "中国", "Russia": "俄罗斯",
            "United Arab Emirates": "阿联酋", "Turkey": "土耳其",
            "Marshall Islands": "马绍尔群岛", "Liberia": "利比里亚",
            "Panama": "巴拿马", "Hong Kong": "香港", "India": "印度",
            "Pakistan": "巴基斯坦", "Afghanistan": "阿富汗",
            "North Korea": "朝鲜", "Cuba": "古巴", "Syria": "叙利亚",
            "Venezuela": "委内瑞拉", "Belarus": "白俄罗斯", "Myanmar": "缅甸",
            "Singapore": "新加坡", "Switzerland": "瑞士",
            "United Kingdom": "英国", "Germany": "德国", "France": "法国",
            "St. Kitts and Nevis": "圣基茨", "Dominica": "多米尼克",
            "Brazil": "巴西", "South Africa": "南非", "Lebanon": "黎巴嫩",
            "Iraq": "伊拉克", "Vietnam": "越南", "Thailand": "泰国",
            "Malaysia": "马来西亚", "Indonesia": "印度尼西亚",
        }
        country_labels = []
        for cc, count in sorted(countries.items(), key=lambda x: -x[1]):
            label = country_names.get(cc, cc)
            country_labels.append(f"{label}({count})")
        lines.append(f"**🌍 涉及国家/地区:** {' · '.join(country_labels[:12])}")
        if len(country_labels) > 12:
            lines.append(f"  ... 等 {len(countries)} 个国家/地区")

    lines.append("")

    # ========== 🇨🇳 中国/香港/澳门相关（重点关注） ==========
    cn_individuals = [e for e in individuals if e.get("china_related")]
    cn_entities = [e for e in entities if e.get("china_related")]
    cn_vessels = [e for e in vessels if e.get("china_related")]
    cn_total = len(cn_individuals) + len(cn_entities) + len(cn_vessels)

    if cn_total > 0:
        lines.append(f"---")
        lines.append(f"## 🇨🇳 ⚠️ 中国/香港/澳门相关制裁 ({cn_total} 项) — 重点关注")
        lines.append("")

        if cn_individuals:
            for i, p in enumerate(cn_individuals, 1):
                name = p["name"]
                country_cn = "/".join(country_names.get(c, c) for c in p.get("countries", []))
                prog_str = ", ".join(p.get("programs", []))
                location = f", {p['location']}" if p.get("location") else ""
                alias_str = ""
                if p.get("aliases"):
                    alias_str = " (又名: %s)" % ", ".join(p["aliases"][:2])
                lines.append(f"🇨🇳 **{name}**{alias_str}")
                detail_parts = []
                if country_cn:
                    detail_parts.append("国籍: %s" % country_cn)
                if location:
                    detail_parts.append("位置: %s" % p["location"])
                if prog_str:
                    detail_parts.append("项目: %s" % prog_str)
                lines.append("   %s" % " · ".join(detail_parts))
                lines.append("")

        if cn_entities:
            for i, e in enumerate(cn_entities, 1):
                name = e["name"]
                country_cn = "/".join(country_names.get(c, c) for c in e.get("countries", []))
                prog_str = ", ".join(e.get("programs", []))
                linked = " → 关联: %s" % e["linked_to"] if e.get("linked_to") else ""
                lines.append("🇨🇳 **%s**%s" % (name, linked))
                detail_parts = []
                if country_cn:
                    detail_parts.append("国家: %s" % country_cn)
                if prog_str:
                    detail_parts.append("项目: %s" % prog_str)
                lines.append("   %s" % " · ".join(detail_parts))
                lines.append("")

        if cn_vessels:
            for i, v in enumerate(cn_vessels, 1):
                name = v["name"]
                vtype = " (%s)" % v["type"] if v.get("type") else ""
                flag = v.get("flag", "")
                imo = " IMO: %s" % v["imo"] if v.get("imo") else ""
                linked = " → %s" % v["linked_to"] if v.get("linked_to") else ""
                lines.append("🇨🇳 **%s**%s%s%s" % (name, vtype, imo, linked))
                detail_parts = []
                if flag:
                    detail_parts.append("船旗: %s" % flag)
                prog_str = ", ".join(v.get("programs", []))
                if prog_str:
                    detail_parts.append("项目: %s" % prog_str)
                if detail_parts:
                    lines.append("   %s" % " · ".join(detail_parts))
                lines.append("")

    # ---------- 新增个人 ----------
    if individuals:
        lines.append(f"---")
        lines.append(f"**👤 新增个人 ({len(individuals)} 人):**")
        lines.append("")
        for i, p in enumerate(individuals[:15], 1):
            name = p["name"]
            country_str = "/".join(p["countries"]) if p["countries"] else ""
            country_cn = "/".join(country_names.get(c, c) for c in p["countries"])
            prog_str = ", ".join(p["programs"]) if p["programs"] else ""
            alias_str = ""
            if p.get("aliases"):
                alias_str = f" (又名: {', '.join(p['aliases'][:2])})"
            loc_str = f", {p['location']}" if p.get("location") else ""
            cn_mark = " 🇨🇳" if p.get("china_related") else ""
            lines.append(f"{i}. **{name}**{alias_str}{cn_mark}")
            detail_parts = []
            if country_cn:
                detail_parts.append(f"国籍: {country_cn}")
            if p.get("location"):
                detail_parts.append(f"位置: {p['location']}")
            if prog_str:
                detail_parts.append(f"项目: {prog_str}")
            lines.append(f"   {' · '.join(detail_parts)}")
        if len(individuals) > 15:
            lines.append(f"   ... 还有 {len(individuals) - 15} 人")
        lines.append("")

    # ---------- 新增实体 ----------
    if entities:
        lines.append(f"---")
        lines.append(f"**🏢 新增实体/组织 ({len(entities)} 个):**")
        lines.append("")
        for i, e in enumerate(entities[:10], 1):
            name = e["name"]
            country_cn = "/".join(country_names.get(c, c) for c in e.get("countries", []))
            prog_str = ", ".join(e.get("programs", []))
            linked = f" → 关联: {e['linked_to']}" if e.get("linked_to") else ""
            cn_mark = " 🇨🇳" if e.get("china_related") else ""
            lines.append(f"{i}. **{name}**{cn_mark}{linked}")
            detail_parts = []
            if country_cn:
                detail_parts.append(f"国家: {country_cn}")
            if prog_str:
                detail_parts.append(f"项目: {prog_str}")
            lines.append(f"   {' · '.join(detail_parts)}")
        if len(entities) > 10:
            lines.append(f"   ... 还有 {len(entities) - 10} 个实体")
        lines.append("")

    # ---------- 新增船只 ----------
    if vessels:
        lines.append(f"---")
        lines.append(f"**🚢 新增船只 ({len(vessels)} 艘):**")
        lines.append("")
        for i, v in enumerate(vessels[:10], 1):
            name = v["name"]
            vtype = f" ({v['type']})" if v.get("type") else ""
            flag = v.get("flag", "")
            imo = f" IMO: {v['imo']}" if v.get("imo") else ""
            linked = f" → {v['linked_to']}" if v.get("linked_to") else ""
            prog_str = ", ".join(v.get("programs", []))
            cn_mark = " 🇨🇳" if v.get("china_related") else ""
            lines.append(f"{i}. **{name}**{cn_mark}{vtype}{imo}{linked}")
            detail_parts = []
            if flag:
                detail_parts.append(f"船旗: {flag}")
            if prog_str:
                detail_parts.append(f"项目: {prog_str}")
            if detail_parts:
                lines.append(f"   {' · '.join(detail_parts)}")
        if len(vessels) > 10:
            lines.append(f"   ... 还有 {len(vessels) - 10} 艘")
        lines.append("")

    # ---------- 名单修改 ----------
    if modifications:
        lines.append(f"---")
        lines.append(f"**✏️ 名单修改 ({len(modifications)} 项):**")
        for i, mod in enumerate(modifications[:10], 1):
            lines.append(f"{i}. {mod}")
        if len(modifications) > 10:
            lines.append(f"   ... 还有 {len(modifications) - 10} 项修改")
        lines.append("")

    # ---------- 行政变更 ----------
    admin = parsed.get("admin_changes", "")
    if admin and admin.lower() != "none":
        lines.append(f"**📝 行政变更:** {admin[:200]}")
        lines.append("")

    # ---------- Treasury 新闻稿 ----------
    press_title = detail.get("press_release_title", "")
    press_url = detail.get("press_release_url", "")
    if press_url:
        lines.append(f"📰 **Treasury 新闻稿:** [{press_title or '链接'}]({press_url})")

    result = "\n".join(lines)
    if len(result) > max_length:
        result = result[:max_length - 100] + "\n\n> ⚠️ 内容过长已截断，请点击原文链接查看完整内容"

    return result
