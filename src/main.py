"""
主程序入口
编排 OFAC 制裁名单的监控和推送流程

主数据来源: OFAC 官方 Recent Actions 页面 (https://ofac.treasury.gov/recent-actions)
备用数据源: OpenSanctions (CDN 加速，提供预计算 delta)
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Config
from src.db import Database
from src.summarizer import Summarizer
from src.notifier import FeishuNotifier
from src.scraper import OFACScraper, summarize_detail_for_push


def setup_logging():
    """配置日志"""
    Config.ensure_dirs()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(Config.LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


# =====================================================================
#  主流程: 从 OFAC 官方 Recent Actions 页面获取数据
# =====================================================================

def cmd_scrape():
    """
    从 OFAC 官方 recent-actions 页面爬取最新制裁行动

    流程:
      1. 爬取列表页（支持分页，防止单页 10 条漏报）
      2. 对比数据库，找出新增行动 + 上次推送失败的待补推行动
      3. 抓取详情页（SDN 名单变更、Treasury 新闻稿链接）
      4. 生成摘要 → 推送到飞书 → 标记已推送
    """
    logger = logging.getLogger("main")
    logger.info("=" * 60)
    logger.info("OFAC 制裁行动监控 - 官方数据源 (recent-actions)")
    logger.info("=" * 60)

    try:
        Config.validate()
    except ValueError as e:
        logger.error(str(e))
        return 1

    scraper = OFACScraper()
    summarizer = Summarizer()
    notifier = FeishuNotifier()

    check_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    check_datetime = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # ========== 第一步：爬取列表页（分页，防止漏报） ==========
    logger.info("[1/5] 爬取 OFAC Recent Actions 列表页（分页）...")
    pagination_error = False
    try:
        all_actions, pagination_error = scraper.fetch_recent_actions(max_pages=3)
    except Exception as e:
        error_msg = f"爬取 OFAC Recent Actions 页面失败: {e}"
        logger.error(error_msg)
        notifier.send_error_alert(error_msg)
        return 1

    if not all_actions:
        logger.warning("未获取到任何行动条目，页面可能改版或网络异常")
        notifier.send_error_alert("OFAC Recent Actions 页面未返回任何条目，请检查页面结构是否变化")
        return 1

    logger.info(f"  获取到 {len(all_actions)} 条近期行动（共 3 页）"
                f"{' ⚠️ 分页不完整' if pagination_error else ''}")

    # ========== 第二步：检测新增 + 补推失败的 ==========
    logger.info("[2/5] 检测新增行动 + 待补推行动...")
    with Database() as db:
        db.init_tables()
        seen_urls = db.get_seen_action_urls()
        # 获取之前推送失败、需要补推的行动
        unpushed_actions = db.get_unpushed_actions()

    new_urls = [a["action_url"] for a in all_actions if a["action_url"] not in seen_urls]
    retry_urls = [a["action_url"] for a in unpushed_actions if a["action_url"] not in new_urls]

    logger.info(
        f"  总计 {len(all_actions)} 条 | "
        f"已记录 {len(seen_urls)} 条 | "
        f"新增 {len(new_urls)} 条 | "
        f"待补推 {len(retry_urls)} 条"
    )

    # 首次运行：标记所有条目为已推送但不发通知（避免一次性推送大量旧数据）
    is_first_run = len(seen_urls) == 0
    if is_first_run:
        # 首次运行 + 分页不完整：仍然建立基线，但标注不完整
        if pagination_error:
            logger.warning(
                "  ⚠️ 首次运行但分页不完整，基线可能不覆盖所有历史条目。"
                "下次运行会补充抓取。"
            )
        logger.info("  🆕 首次运行，建立基线（不推送历史数据）")
        with Database() as db:
            for action in all_actions:
                db.save_action({
                    "action_url": action["action_url"],
                    "title": action["title"],
                    "action_date": action["action_date"],
                    "category": action.get("category", ""),
                    "category_url": action.get("category_url", ""),
                    "press_release_url": "",
                    "press_release_title": "",
                    "body_text": "",
                    "pushed": 1,
                })

        logger.info("[5/5] 首次运行基线已建立，不发送状态报告")
        return 0

    # 无新增且无补推
    if not new_urls and not retry_urls:
        if pagination_error:
            # 分页不完整 → 不确定是否真的"无变化"，不写死状态，下次运行会重新检测
            logger.warning(
                "  分页抓取不完整，跳过写入'无变化'状态。"
                "本次不推送状态报告，下次运行会重新检测所有页面。"
            )
            return 1  # 非零退出让 CI 感知到异常
        logger.info("  无新增行动，无待补推行动")
        with Database() as db:
            db.save_monitor_state(
                check_date, "ofac-scrape", "",
                len(all_actions), len(all_actions),
                changes_found=0, delta_processed=0,
            )
        logger.info("[5/5] 无新增行动，不发送状态报告")
        return 0

    # 有新增或补推，但分页不完整 — 记录警告，仍然处理已有数据
    if pagination_error:
        logger.warning(
            "  ⚠️ 分页抓取不完整，当前仅基于部分结果检测新增。"
            "后续页可能还有未抓取的新行动，下次运行会补抓。"
        )

    # ========== 第三步：构建需要获取详情的行动列表 ==========
    # 新行动从 all_actions 中取；补推行动从 unpushed_actions (DB) 中取
    actions_to_process = []

    # 新发现的行动（来自列表页）
    for a in all_actions:
        if a["action_url"] in new_urls:
            actions_to_process.append(a)

    # 补推的行动（来自数据库记录，已有完整信息）
    for ua in unpushed_actions:
        if ua["action_url"] in retry_urls:
            # 带上数据库中已有的 body_html 和 body_text，补推时优先复用
            actions_to_process.append({
                "action_url": ua["action_url"],
                "title": ua["title"],
                "action_date": ua["action_date"],
                "category": ua.get("category", ""),
                "category_url": ua.get("category_url", ""),
                "press_release_url": ua.get("press_release_url", ""),
                "press_release_title": ua.get("press_release_title", ""),
                "body_html": ua.get("body_html", ""),
                "body_text": ua.get("body_text", ""),
                "_is_retry": True,
            })

    logger.info(
        f"[3/5] 需要获取详情的行动: {len(actions_to_process)} 条 "
        f"(新增 {len(new_urls)}, 补推 {len(retry_urls)})"
    )

    # ========== 第四步：获取详情（仅对没有 body_text 的） ==========
    enriched_actions = []

    for i, action in enumerate(actions_to_process):
        action_url = action["action_url"]
        is_retry = action.get("_is_retry", False)
        label = "补推" if is_retry else "新增"
        logger.info(f"  [{i+1}/{len(actions_to_process)}] 获取详情 ({label}): {action_url}")

        # 补推记录：如果数据库已有 body_html，直接复用生成结构化摘要
        # 否则重新抓取详情页获取 body_html
        if is_retry and action.get("body_html"):
            detail = {
                "body_html": action["body_html"],
                "body_text": action.get("body_text", ""),
                "press_release_url": action.get("press_release_url", ""),
                "press_release_title": action.get("press_release_title", ""),
                "sections": {},
            }
            action["detail_summary"] = summarize_detail_for_push(detail)
            action["_reused_body_html"] = True
            enriched_actions.append(action)
            continue

        try:
            detail = scraper.fetch_action_detail(action_url)
            action["press_release_url"] = detail.get("press_release_url", "")
            action["press_release_title"] = detail.get("press_release_title", "")
            action["body_html"] = detail.get("body_html", "")
            action["body_text"] = detail.get("body_text", "")
            action["body_sections"] = detail.get("sections", {})

            detail_summary = summarize_detail_for_push(detail)
            action["detail_summary"] = detail_summary

            enriched_actions.append(action)
        except Exception as e:
            logger.warning(f"  获取详情失败: {e}，使用已有数据")
            if not action.get("detail_summary"):
                # 回退：用已有的 body_html 或 body_text 生成摘要
                fallback_detail = {
                    "body_html": action.get("body_html", ""),
                    "body_text": action.get("body_text", ""),
                    "press_release_url": action.get("press_release_url", ""),
                    "press_release_title": action.get("press_release_title", ""),
                    "sections": {},
                }
                action["detail_summary"] = summarize_detail_for_push(fallback_detail)
            enriched_actions.append(action)

    # 保存到数据库（使用 ON CONFLICT DO UPDATE，不会覆盖 pushed 状态和 first_seen_at）
    logger.info("  保存行动记录到数据库...")
    with Database() as db:
        for action in enriched_actions:
            db_action = {
                "action_url": action["action_url"],
                "title": action["title"],
                "action_date": action["action_date"],
                "category": action.get("category", ""),
                "category_url": action.get("category_url", ""),
                "press_release_url": action.get("press_release_url", ""),
                "press_release_title": action.get("press_release_title", ""),
                "body_html": action.get("body_html", ""),
                "body_text": action.get("body_text", ""),
                "pushed": 0,
            }
            db.save_action(db_action)

        db.save_monitor_state(
            check_date, "ofac-scrape", "",
            len(all_actions), len(all_actions),
            changes_found=len(actions_to_process),
            delta_processed=0,
        )

    # ========== 第五步：生成摘要并推送 ==========
    logger.info("[5/5] 生成摘要并推送到飞书...")

    summary = summarizer.summarize_scraped_actions(
        enriched_actions, check_datetime, len(all_actions), len(seen_urls)
    )

    success = notifier.send_update_notification(summary)

    if success:
        # 标记所有为已推送
        with Database() as db:
            for action in enriched_actions:
                db.mark_action_pushed(action["action_url"])
        logger.info("✅ 监控任务完成")
        return 0
    else:
        # 推送失败 — 记录仍保留 pushed=0，下次运行时会自动补推
        logger.error("❌ 飞书推送失败（记录已保存，下次运行将自动补推）")
        return 1


def _build_first_run_report(actions: list, check_datetime: str) -> str:
    """首次运行报告"""
    lines = []
    lines.append(f"## 🏛️ OFAC 制裁行动监控 - 首次运行\n")
    lines.append(f"**数据来源**: [ofac.treasury.gov/recent-actions](https://ofac.treasury.gov/recent-actions)")
    lines.append(f"**检测时间**: {check_datetime}\n")
    lines.append(f"---\n")
    lines.append(f"✅ 系统已建立监控基线。\n")
    lines.append(f"**当前页面共有 {len(actions)} 条近期行动:**\n")

    # 按类别统计
    from collections import Counter
    cats = Counter(a.get("category", "未分类") for a in actions)
    for cat, count in cats.most_common():
        lines.append(f"- {cat}: {count} 条")

    lines.append(f"\n---\n")
    lines.append(f"### 📋 当前列表页最新行动\n")

    for i, a in enumerate(actions[:5], 1):
        title = a.get("title", "N/A")
        date = a.get("date_raw", "")
        cat = a.get("category", "")
        url = f"https://ofac.treasury.gov{a['action_url']}" if a.get("action_url") else ""
        lines.append(f"{i}. **[{title}]({url})**")
        lines.append(f"   📅 {date}  |  {cat}")
        lines.append("")

    lines.append(f"> 后续检测到新行动时将自动推送详细通知。")
    lines.append(f"\n📌 数据来源: [OFAC Recent Actions](https://ofac.treasury.gov/recent-actions)")
    lines.append(f"⏰ 检测时间: {check_datetime}")

    return "\n".join(lines)


def _build_no_changes_report(total_count: int, check_datetime: str) -> str:
    """无变化时的报告"""
    lines = []
    lines.append(f"## 📊 OFAC 制裁行动监测\n")
    lines.append(f"**检测时间**: {check_datetime}\n")
    lines.append(f"✅ 本次检测未发现新的制裁行动。\n")
    lines.append(f"> 当前页面共 {total_count} 条近期行动，均已被记录。")
    lines.append(f"\n📌 数据来源: [OFAC Recent Actions](https://ofac.treasury.gov/recent-actions)")
    return "\n".join(lines)


# =====================================================================
#  备用流程: OpenSanctions 数据源
# =====================================================================

def cmd_run():
    """
    兼容旧部署入口。

    OpenSanctions 数据源已禁用。保留 run 命令只是为了让旧的 cron/systemd
    不会误触发备用数据源；真正监控和推送只通过 scrape 命令进行。
    """
    logger = logging.getLogger("main")
    logger.info("=" * 60)
    logger.info("OpenSanctions 数据源已禁用；请使用 scrape 命令")
    logger.info("=" * 60)
    logger.warning("跳过执行：OpenSanctions 备用流程已彻底关闭，不会抓取或推送")
    logger.info("如需执行每日监控，请运行: python3 -m src.main scrape")
    return 0


# =====================================================================
#  其他命令
# =====================================================================

def cmd_test():
    """发送测试消息到飞书"""
    logger = logging.getLogger("main")
    logger.info("发送测试消息到飞书...")

    try:
        Config.validate()
    except ValueError as e:
        logger.error(str(e))
        return 1

    notifier = FeishuNotifier()
    success = notifier.send_test_message()

    if success:
        logger.info("✅ 测试消息发送成功！请检查您的飞书群聊。")
        return 0
    else:
        logger.error("❌ 测试消息发送失败，请检查 Webhook URL 和网络连接。")
        return 1


def cmd_status():
    """查看当前监控状态"""
    Config.ensure_dirs()

    with Database() as db:
        db.init_tables()

        # OFAC 官方数据
        latest_action_date = db.get_latest_action_date()
        recent_ofac = db.get_recent_ofac_actions(10)
        unpushed = db.get_unpushed_actions()

        # OpenSanctions 历史数据
        latest_check = db.get_latest_check_date()

        print("=" * 55)
        print("📊 OFAC 监控系统状态")
        print("=" * 55)
        print(f"  主数据源: OFAC 官方 Recent Actions")
        print(f"  最新行动日期: {latest_action_date or 'N/A'}")
        print(f"  已记录行动: {len(recent_ofac)} 条 (最近)")
        print(f"  待推送: {len(unpushed)} 条")
        print(f"  最近 OpenSanctions 检查: {latest_check or 'N/A'}")
        print(f"  数据库路径: {Config.DB_PATH}")
        print(f"  日志路径: {Config.LOG_FILE}")
        print(f"  数据目录: {Config.DATA_DIR}")
        print("=" * 55)

        if recent_ofac:
            print()
            print("📋 最近记录的 OFAC 行动:")
            for i, a in enumerate(recent_ofac[:5], 1):
                pushed = "✅" if a.get("pushed") else "⏳"
                print(f"  {i}. {pushed} [{a['action_date']}] {a['title'][:60]}")
                if a.get("press_release_title"):
                    print(f"     📰 {a['press_release_title'][:60]}")

        print()
        print("命令参考:")
        print("  python3 -m src.main scrape   运行官方数据源监控 (推荐)")
        print("  python3 -m src.main run      已禁用的旧入口 (不会抓取或推送)")
        print("  python3 -m src.main test     测试飞书推送")
        print("  python3 -m src.main status   查看此状态")

    return 0


# =====================================================================
#  入口
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        description="OFAC 制裁名单每日监控系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python3 -m src.main scrape   从 OFAC 官方页面爬取最新制裁行动 (推荐，推送飞书)
  python3 -m src.main run      已禁用的旧入口 (不会抓取或推送)
  python3 -m src.main test     发送测试消息到飞书
  python3 -m src.main status   查看监控状态
        """,
    )

    parser.add_argument(
        "command",
        choices=["scrape", "run", "test", "status"],
        default="scrape",
        nargs="?",
        help="执行命令: scrape(OFAC官方数据源,推送), run(已禁用旧入口), test(测试推送), status(查看状态)",
    )

    args = parser.parse_args()

    setup_logging()

    commands = {
        "scrape": cmd_scrape,
        "run": cmd_run,
        "test": cmd_test,
        "status": cmd_status,
    }

    return commands[args.command]()


if __name__ == "__main__":
    sys.exit(main())
