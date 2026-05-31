# OFAC 制裁名单每日监控系统

## 📋 项目简介

本系统自动监控美国财政部 OFAC（外国资产控制办公室）的 SDN（Specially Designated Nationals）制裁名单更新，每日检测变更并推送通知到飞书群聊。

**数据来源**: [OpenSanctions](https://www.opensanctions.org/datasets/us_ofac_sdn/)（CDN 全球加速，提供预计算的 delta 变更数据）

### 核心功能

- 🔍 **秒级检测**: 基于 OpenSanctions CDN，全流程 2 秒内完成（vs. OFAC 官方下载需 30+ 分钟）
- 📊 **精准变更**: 利用预计算的 delta 文件，精确识别 新增/修改/移除 的实体
- 📱 **飞书推送**: 美观的交互卡片消息，包含变更摘要和详细列表
- 📝 **历史追踪**: SQLite 数据库存储所有检查记录和变更历史

## 🚀 快速开始

### 环境要求

- Python 3.8+
- macOS / Linux
- 飞书机器人 Webhook 地址

### 安装步骤

```bash
cd ofac-monitor
bash setup.sh                           # 一键安装
nano .env                               # 确认飞书 Webhook URL
python3 -m src.main test                # 测试飞书推送
python3 -m src.main run                 # 运行监控
```

### 设置每日定时任务

```bash
crontab -e
# 添加：每天早上 9:00 自动执行
0 9 * * * /Users/littlecuteh/Desktop/hw-project/ofac-monitor/run.sh
```

## 📁 项目结构

```
ofac-monitor/
├── src/
│   ├── config.py                       # 配置管理
│   ├── db.py                           # 数据库操作
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
| `python3 -m src.main run` | 运行一次完整监控检查 |
| `python3 -m src.main test` | 发送测试消息到飞书 |
| `python3 -m src.main status` | 查看监控状态 |
| `bash run.sh` | Shell 脚本封装，带日志记录 |

## 📊 工作流程

```
1. 获取 OpenSanctions index.json  (4KB,  ~0.5s)
2. 检查数据版本是否变化
3. 如有新版本 → 下载 entities.delta.json  (~50KB, ~0.2s)
4. 解析 delta → 获取 ADD/MOD/DEL 变更
5. 生成摘要 → Markdown 格式
6. 推送到飞书 → 交互卡片消息
```

## 📖 相关链接

- [OpenSanctions OFAC SDN](https://www.opensanctions.org/datasets/us_ofac_sdn/)
- [OFAC 官网](https://ofac.treasury.gov/)
- [OFAC 制裁搜索](https://sanctionssearch.ofac.treas.gov/)
- [飞书机器人文档](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)
