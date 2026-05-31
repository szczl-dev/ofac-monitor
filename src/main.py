"""
主程序入口
编排 OFAC 制裁名单的监控和推送流程

数据来源: OpenSanctions (CDN 加速，提供预计算 delta)
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
from src.fetcher import Fetcher
from src.parser_opensanctions import OpenSanctionsParser
from src.summarizer import Summarizer
from src.notifier import FeishuNotifier


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


def cmd_run():
    """
    运行一次监控检查
    流程:
      1. 获取 OpenSanctions index.json → 判断是否有更新
      2. 如有更新 → 下载 entities.delta.json → 解析变更
      3. 生成摘要 → 推送到飞书
    """
    logger = logging.getLogger("main")
    logger.info("=" * 60)
    logger.info("OFAC 制裁名单监控 - 开始运行")
    logger.info("=" * 60)

    try:
        Config.validate()
    except ValueError as e:
        logger.error(str(e))
        return 1

    fetcher = Fetcher()
    parser = OpenSanctionsParser()
    summarizer = Summarizer()
    notifier = FeishuNotifier()

    check_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ==================== 第一步：获取数据集索引 ====================
    logger.info("[1/4] 获取 OpenSanctions 数据集索引...")
    try:
        index_info = fetcher.fetch_index()
    except Exception as e:
        error_msg = f"获取数据集索引失败: {e}"
        logger.error(error_msg)
        notifier.send_error_alert(error_msg)
        return 1

    version = index_info.get("version", "")
    last_change = index_info.get("last_change", "")
    entity_count = index_info.get("entity_count", 0)
    target_count = index_info.get("target_count", 0)

    logger.info(f"  版本: {version}")
    logger.info(f"  最后变更: {last_change}")
    logger.info(f"  实体数: {entity_count:,}  制裁目标: {target_count:,}")

    # ==================== 第二步：检查是否有更新 ====================
    logger.info("[2/4] 检查是否需要获取变更...")
    with Database() as db:
        db.init_tables()
        prev_version = db.get_latest_version()
        is_first_run = prev_version is None

    if prev_version == version:
        logger.info(f"  版本未变化 ({version})，无需更新")
        # 保存状态但标记无变化
        with Database() as db:
            db.save_monitor_state(
                check_date, version, last_change,
                entity_count, target_count, changes_found=0
            )
        # 发送状态报告
        report = summarizer._no_changes_summary(index_info, check_date)
        logger.info("[4/4] 推送状态报告...")
        success = notifier.send_status_report(report)
        return 0 if success else 1

    if is_first_run:
        logger.info(f"  首次运行，建立基线 (版本: {version})")
    else:
        logger.info(f"  检测到新版本: {prev_version} → {version}")

    # ==================== 第三步：获取 delta 变更 ====================
    logger.info("[3/4] 获取实体变更数据...")
    try:
        delta_changes = fetcher.fetch_entities_delta(version)
    except Exception as e:
        error_msg = f"获取 delta 变更数据失败: {e}"
        logger.error(error_msg)
        notifier.send_error_alert(error_msg)
        return 1

    # 解析 delta
    changes_for_db, stats = parser.parse_delta_changes(
        delta_changes, check_date, version
    )

    # 保存到数据库
    logger.info("  保存变更记录到数据库...")
    with Database() as db:
        if changes_for_db:
            db.save_changes_batch(changes_for_db)
            logger.info(f"  已保存 {len(changes_for_db)} 条变更记录")

        db.save_monitor_state(
            check_date, version, last_change,
            entity_count, target_count,
            changes_found=stats["total"],
            delta_processed=1,
        )

    # ==================== 第四步：生成摘要并推送 ====================
    logger.info("[4/4] 生成摘要并推送到飞书...")

    if stats["total"] > 0:
        # 有变更：发送更新通知（包括首次运行）
        with Database() as db:
            full_changes = db.get_changes_by_date(check_date)
        summary = summarizer.generate_summary(
            full_changes, index_info, stats, check_date
        )
        prefix = "🆕 首次运行 - " if is_first_run else ""
        summary = prefix + summary
        success = notifier.send_update_notification(summary)
    elif is_first_run:
        # 首次运行且无变更：发送状态报告
        report = summarizer.generate_status_report(index_info, check_date)
        success = notifier.send_status_report(report)
    else:
        # 无变更
        report = summarizer._no_changes_summary(index_info, check_date)
        success = notifier.send_status_report(report)

    if success:
        logger.info("✅ 监控任务完成")
        return 0
    else:
        logger.error("❌ 飞书推送失败")
        return 1


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
        latest_check = db.get_latest_check_date()

        if latest_check is None:
            print("📭 尚未执行过监控，请先运行: python3 -m src.main run")
            return 0

        recent_changes = db.get_recent_changes(20)

        print("=" * 55)
        print("📊 OFAC 监控系统状态")
        print("=" * 55)
        print(f"  最近检查日期: {latest_check}")
        print(f"  最近变更记录: {len(recent_changes)} 条")
        print(f"  数据库路径: {Config.DB_PATH}")
        print(f"  日志路径: {Config.LOG_FILE}")
        print(f"  数据目录: {Config.DATA_DIR}")
        print("=" * 55)
        print()
        print("命令参考:")
        print("  python3 -m src.main run     运行监控检查")
        print("  python3 -m src.main test    测试飞书推送")
        print("  python3 -m src.main status  查看此状态")

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="OFAC 制裁名单每日监控系统（基于 OpenSanctions 数据）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python3 -m src.main run      运行一次监控检查
  python3 -m src.main test     发送测试消息到飞书
  python3 -m src.main status   查看监控状态
        """,
    )

    parser.add_argument(
        "command",
        choices=["run", "test", "status"],
        default="run",
        nargs="?",
        help="执行命令: run(运行监控), test(测试推送), status(查看状态)",
    )

    args = parser.parse_args()

    setup_logging()

    commands = {
        "run": cmd_run,
        "test": cmd_test,
        "status": cmd_status,
    }

    return commands[args.command]()


if __name__ == "__main__":
    sys.exit(main())
