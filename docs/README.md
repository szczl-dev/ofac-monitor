# OFAC 制裁名单每日监控系统

## 📋 项目简介

本系统自动监控美国财政部 OFAC（外国资产控制办公室）的制裁行动更新，每日从 OFAC 官方 Recent Actions 页面获取最新数据，检测新增制裁行动并推送通知到飞书群聊。

**主要数据来源**: [OFAC Recent Actions](https://ofac.treasury.gov/recent-actions)（官方权威，每日更新制裁行动、SDN 名单变更、执法行动等）

**备用数据来源**: [OpenSanctions](https://www.opensanctions.org/datasets/us_ofac_sdn/)（CDN 全球加速，提供预计算的 delta 变更数据）

### 核心功能

- 🏛️ **官方直接**: 直接从 OFAC 官网抓取，获取最准确的制裁行动信息
- 🔗 **完整链接**: 包含 Treasury 新闻稿链接和 OFAC 详情页链接
- 📋 **智能摘要**: 自动提取 SDN 名单变更中的实体名称、类型和数量统计
- 📱 **飞书推送**: 美观的交互卡片消息，包含变更摘要和详细列表
- 📝 **去重追踪**: SQLite 数据库存储所有已见行动，只推送新增内容

## 🚀 快速开始

### 环境要求

- Python 3.8+
- macOS / Linux
- 飞书机器人 Webhook 地址

### 安装步骤

```bash
cd ofac-monitor
pip3 install -r requirements.txt       # 安装依赖
cp .env.example .env                   # 创建配置文件
nano .env                              # 填入飞书 Webhook URL
python3 -m src.main test               # 测试飞书推送
python3 -m src.main scrape             # 运行监控 (官方数据源)
```

### 设置每日定时任务

```bash
crontab -e
# 添加：每天早上 9:00 自动执行
0 9 * * * /path/to/ofac-monitor/run.sh
```

## 📁 项目结构

```
ofac-monitor/
├── src/
│   ├── config.py                       # 配置管理
│   ├── db.py                           # 数据库操作
│   ├── scraper.py                      # OFAC 官方页面爬取 (新增)
│   ├── fetcher.py                      # 数据下载 (OpenSanctions CDN)
│   ├── parser_opensanctions.py         # Delta 数据解析
│   ├── parser.py                       # OFAC XML 解析（备用）
│   ├── summarizer.py                   # 摘要生成
│   ├── notifier.py                     # 飞书推送
│   └── main.py                         # 主程序
├── data/                               # 数据目录
├── logs/                               # 日志
├── docs/                               # 文档
├── .env                                # 配置文件
├── requirements.txt                    # Python 依赖
├── setup.sh                            # 安装脚本
└── run.sh                              # 运行脚本
```

## 🛠️ 命令说明

| 命令 | 说明 |
|------|------|
| `python3 -m src.main scrape` | 从 OFAC 官方页面爬取最新制裁行动 (推荐，推送飞书) |
| `python3 -m src.main run` | 使用 OpenSanctions 数据源 (备用，仅记录不推送) |
| `python3 -m src.main test` | 发送测试消息到飞书 |
| `python3 -m src.main status` | 查看监控状态 |
| `bash run.sh` | Shell 脚本封装，带日志记录 |

## 📊 工作流程

```
1. 爬取 https://ofac.treasury.gov/recent-actions 列表页 (~42KB, ~1s)
2. 对比数据库，检测新增行动条目
3. 对新行动抓取详情页 → 提取 SDN 名单变更、Treasury 新闻稿链接
4. 生成 Markdown 摘要 → 包含实体统计和关键信息
5. 推送到飞书 → 交互卡片消息
```

## 📖 相关链接

- [OFAC Recent Actions](https://ofac.treasury.gov/recent-actions) - 官方制裁行动页面
- [OFAC 官网](https://ofac.treasury.gov/)
- [OFAC 制裁搜索](https://sanctionssearch.ofac.treas.gov/)
- [OpenSanctions OFAC SDN](https://www.opensanctions.org/datasets/us_ofac_sdn/)
- [飞书机器人文档](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)
