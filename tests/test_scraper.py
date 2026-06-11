"""
解析器单元测试
验证 OFAC 详情页 HTML 解析的准确性，防止页面改版或正则修改导致静默漏实体。

测试样本: tests/fixtures/sample_detail_page.html
预期: 4 个人, 12 个实体, 6 艘船, 2 项修改
"""

import json
import os
import sys
import unittest
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scraper import (
    OFACScraper,
    parse_sanctioned_entities,
    summarize_detail_for_push,
    _parse_date,
    _split_html_entries,
)


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class TestDateParsing(unittest.TestCase):
    """日期解析测试"""

    def test_standard_format(self):
        """Month DD, YYYY 格式"""
        self.assertEqual(_parse_date("June 05, 2026"), "2026-06-05")
        self.assertEqual(_parse_date("May 28, 2026"), "2026-05-28")
        self.assertEqual(_parse_date("January 01, 2024"), "2024-01-01")
        self.assertEqual(_parse_date("December 31, 2023"), "2023-12-31")

    def test_slash_format(self):
        """MM/DD/YYYY 格式"""
        self.assertEqual(_parse_date("06/05/2026"), "2026-06-05")

    def test_empty(self):
        """空字符串"""
        self.assertEqual(_parse_date(""), "")
        self.assertEqual(_parse_date(None), "")


class TestEntityParsing(unittest.TestCase):
    """制裁实体解析测试 — 核心回归测试

    这是最重要的测试类：每次修改 scraper.py 的解析逻辑后，
    必须运行此测试确保:
      1. 实体数量不会减少（防止 #11→#12 类 bug）
      2. 公司后缀不会丢失（LTD., INC., LIMITED, L.L.C. 等）
      3. 船名中的数字不会丢失（MD 23 而非 MD）
      4. HTML 实体（&amp;）被正确解码
      5. 中国相关实体被正确标记
    """

    @classmethod
    def setUpClass(cls):
        """加载测试样本"""
        fixture_path = FIXTURES_DIR / "sample_detail_page.html"
        if not fixture_path.exists():
            raise FileNotFoundError(f"测试样本不存在: {fixture_path}")
        cls.fixture_html = fixture_path.read_text(encoding="utf-8")

        # 模拟详情页结构
        cls.detail = {
            "action_url": "/recent-actions/20260605",
            "date_mm_dd_yyyy": "06/05/2026",
            "press_release_url": "https://home.treasury.gov/press-release-123",
            "press_release_title": "Iran-related Designations; Counter Terrorism Updates",
            "body_html": cls.fixture_html,
            "body_text": "",
            "sections": {},
        }
        cls.parsed = parse_sanctioned_entities(cls.detail)

    # ================================================================
    #  数量回归测试 — 最高优先级！任何数量减少都是回归
    # ================================================================

    def test_individual_count(self):
        """个人数量: 4"""
        self.assertEqual(len(self.parsed["individuals"]), 4,
                         f"期望 4 个个人，实际 {len(self.parsed['individuals'])} 个")

    def test_entity_count(self):
        """实体数量: 12（回归 #11→#12 bug）"""
        self.assertEqual(len(self.parsed["entities"]), 12,
                         f"期望 12 个实体，实际 {len(self.parsed['entities'])} 个")

    def test_vessel_count(self):
        """船只数量: 6"""
        self.assertEqual(len(self.parsed["vessels"]), 6,
                         f"期望 6 艘船只，实际 {len(self.parsed['vessels'])} 艘")

    def test_modification_count(self):
        """修改项数量: 2"""
        self.assertEqual(len(self.parsed["modifications"]), 2,
                         f"期望 2 项修改，实际 {len(self.parsed['modifications'])} 项")

    # ================================================================
    #  名称提取回归测试
    # ================================================================

    def test_entity_name_with_ltd(self):
        """CO., LTD. 后缀应完整保留（上海企业，曾遗漏）"""
        names = [e["name"] for e in self.parsed["entities"]]
        self.assertIn("SHANGHAI QIANYE ENERGY CO., LTD.", names,
                      "CO., LTD. 后缀应完整保留（回归 #11→#12 bug）")

    def test_entity_name_with_limited(self):
        """LIMITED 后缀"""
        names = [e["name"] for e in self.parsed["entities"]]
        self.assertIn("DELTA TRADING CO., LIMITED", names)

    def test_entity_name_with_llc(self):
        """L.L.C. 后缀"""
        names = [e["name"] for e in self.parsed["entities"]]
        self.assertIn("ALPHA DEFENSE SOLUTIONS L.L.C.", names)

    def test_entity_name_with_incorporated(self):
        """INCORPORATED 后缀"""
        names = [e["name"] for e in self.parsed["entities"]]
        self.assertIn("GOLDEN PEARL ENTERPRISES INCORPORATED", names)

    def test_entity_name_with_fze(self):
        """FZE 后缀（中东常见公司形式）"""
        names = [e["name"] for e in self.parsed["entities"]]
        self.assertIn("PERSIAN GULF TRADING FZE", names)

    def test_vessel_name_with_digits(self):
        """船只名称包含数字: MD 23（曾因 \w 不含数字被截断为 MD）"""
        names = [v["name"] for v in self.parsed["vessels"]]
        found = any("MD 23" in n for n in names)
        self.assertTrue(found,
                        f"应包含 MD 23，实际船只名称: {names}")

    def test_html_entity_decoding(self):
        """HTML 实体解码: &amp; → &"""
        names = [e["name"] for e in self.parsed["entities"]]
        found = any("&" in n for n in names)
        self.assertTrue(found,
                        f"&amp; 应被解码为 &，实际: {[n for n in names if 'SHIPPING' in n]}")

    # ================================================================
    #  中国/香港/澳门针对性检测
    # ================================================================

    def test_china_related_entities(self):
        """中国相关实体检测: 上海企业 + 香港企业"""
        cn_entities = [e for e in self.parsed["entities"] if e.get("china_related")]
        cn_names = [e["name"] for e in cn_entities]
        self.assertIn("SHANGHAI QIANYE ENERGY CO., LTD.", cn_names,
                      f"上海实体应标记为 china_related")
        self.assertIn("DELTA TRADING CO., LIMITED", cn_names,
                      f"香港实体应标记为 china_related")

    def test_china_related_individual(self):
        """中国国籍个人应标记"""
        cn_individuals = [p for p in self.parsed["individuals"] if p.get("china_related")]
        self.assertGreaterEqual(len(cn_individuals), 1,
                                f"应有至少 1 个中国相关个人，实际: {len(cn_individuals)}")

    # ================================================================
    #  制裁项目标签
    # ================================================================

    def test_program_tags(self):
        """制裁项目标签提取"""
        all_entities = (
            self.parsed["individuals"] +
            self.parsed["entities"] +
            self.parsed["vessels"]
        )
        all_programs = set()
        for e in all_entities:
            all_programs.update(e.get("programs", []))
        self.assertIn("IRAN-EO13902", all_programs)
        self.assertIn("SDGT", all_programs)
        self.assertIn("RUSSIA-EO14024", all_programs)

    # ================================================================
    #  船只详细信息
    # ================================================================

    def test_vessel_imo_extraction(self):
        """船只 IMO 号码提取"""
        vessels = self.parsed["vessels"]
        imos = [v["imo"] for v in vessels if v.get("imo")]
        self.assertIn("9567890", imos)
        self.assertIn("9123456", imos)
        self.assertEqual(len(imos), 6, "所有 6 艘船都应有 IMO")

    def test_vessel_type_extraction(self):
        """船只类型提取"""
        vessels = self.parsed["vessels"]
        types = [v["type"] for v in vessels if v.get("type")]
        self.assertIn("Oil Tanker", types)
        self.assertIn("LPG Tanker", types)

    # ================================================================
    #  汇总统计
    # ================================================================

    def test_program_statistics(self):
        """制裁项目统计"""
        programs = self.parsed["programs"]
        self.assertIn("IRAN-EO13902", programs)
        self.assertGreater(programs["IRAN-EO13902"], 5,
                           "IRAN-EO13902 应涉及多个实体")

    def test_country_statistics(self):
        """国家统计"""
        countries = self.parsed["countries"]
        self.assertIn("China", countries)
        self.assertIn("United Arab Emirates", countries)


class TestHTMLSplitter(unittest.TestCase):
    """HTML 条目分割测试"""

    def test_split_by_double_br(self):
        """按 <br><br> 分割多个条目（需要足够长才能通过最小长度过滤）"""
        # _split_html_entries 要求 clean 后 > 10 字符，短文本会被过滤
        html = ("ENTRY NUMBER ONE WITH DETAILS<br><br>"
                "ENTRY NUMBER TWO WITH DETAILS<br><br>"
                "ENTRY NUMBER THREE WITH MORE")
        result = _split_html_entries(html)
        self.assertEqual(len(result), 3,
                         f"应分割为 3 个条目，实际: {result}")

    def test_split_with_inline_tags(self):
        """内联标签不影响分割"""
        html = ("<strong>NAME A COMPANY</strong> based in Iran<br><br>"
                "<em>NAME B ENTITY</em> based in China")
        result = _split_html_entries(html)
        self.assertEqual(len(result), 2)
        self.assertIn("NAME A COMPANY", result[0])
        self.assertIn("NAME B ENTITY", result[1])

    def test_split_single_entry(self):
        """单个条目"""
        html = "A SINGLE ENTRY WITH DETAILS HERE"
        result = _split_html_entries(html)
        self.assertEqual(len(result), 1)

    def test_html_entity_unescape(self):
        """HTML 实体解码"""
        html = ("SHANGHAI QIANYE ENERGY CO., LTD. &amp; TRADING<br><br>"
                "NEXT ENTRY HERE WITH INFO")
        result = _split_html_entries(html)
        self.assertIn("&", result[0])
        self.assertNotIn("&amp;", result[0])

    def test_short_entries_filtered(self):
        """短于 10 字符的条目被过滤"""
        html = "AB<br><br>CDEFGHIJKLMNOP"
        result = _split_html_entries(html)
        # "AB" < 10 chars → filtered; "CDEFGHIJKLMNOP" >= 10 chars → kept
        self.assertEqual(len(result), 1)


class TestSummarizeDetail(unittest.TestCase):
    """摘要生成测试"""

    @classmethod
    def setUpClass(cls):
        fixture_path = FIXTURES_DIR / "sample_detail_page.html"
        cls.fixture_html = fixture_path.read_text(encoding="utf-8")
        cls.detail = {
            "action_url": "/recent-actions/20260605",
            "press_release_url": "https://home.treasury.gov/press-release-123",
            "press_release_title": "Test Press Release",
            "body_html": cls.fixture_html,
            "body_text": "",
            "sections": {},
        }

    def test_summary_contains_china_highlight(self):
        """摘要包含中国重点关注区块"""
        summary = summarize_detail_for_push(self.detail)
        self.assertIn("🇨🇳", summary, "摘要应包含中国高亮标记")
        self.assertIn("中国/香港/澳门相关制裁", summary,
                      "摘要应有中国重点关注标题")

    def test_summary_contains_entity_counts(self):
        """摘要包含实体数量"""
        summary = summarize_detail_for_push(self.detail)
        self.assertIn("12 个", summary, "应显示实体数量 12")

    def test_summary_contains_original_names(self):
        """实体名称保持原始英文（不翻译）"""
        summary = summarize_detail_for_push(self.detail)
        self.assertIn("SHANGHAI QIANYE ENERGY CO., LTD.", summary)
        self.assertIn("SMITH, John", summary)

    def test_summary_not_too_long(self):
        """摘要不超过长度限制"""
        summary = summarize_detail_for_push(self.detail, max_length=3000)
        self.assertLessEqual(len(summary), 3100,
                             f"摘要长度 {len(summary)} 超过限制 3100")

    def test_summary_contains_program_info(self):
        """摘要包含制裁项目信息"""
        summary = summarize_detail_for_push(self.detail)
        self.assertIn("制裁项目", summary)
        self.assertIn("IRAN", summary)


if __name__ == "__main__":
    unittest.main(verbosity=2)
