# OFAC 监控系统 - 架构文档

## 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    定时调度 (cron)                       │
│                    run.sh / 每日9:00                      │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│                   main.py (编排器)                       │
│  ┌─────────────────────────────────────────────────────┐│
│  │ 1. 下载 SDN XML        → fetcher.py                ││
│  │ 2. 解析 XML            → parser.py                 ││
│  │ 3. 存储快照            → db.py (SQLite)             ││
│  │ 4. 检测变更            → detector.py               ││
│  │ 5. 生成摘要            → summarizer.py              ││
│  │ 6. 推送飞书            → notifier.py               ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
```

## 数据流

```
OFAC 官网                   本地                     飞书
─────────                  ──────                   ────
sdn.xml ──下载──▶  data/current/sdn_YYYY-MM-DD.xml
                           │
                           ▼ 解析
                    entries[] (内存)
                           │
                    ┌──────┴──────┐
                    ▼              ▼
              SQLite 存储      SQLite 对比
              (快照表)        (变更检测)
                                   │
                                   ▼
                            changes[]
                                   │
                                   ▼ 摘要生成
                            Markdown 文本
                                   │
                                   ▼ 发送
                              飞书群聊
```

## 模块说明

### config.py
- 加载 `.env` 环境变量
- 定义所有配置常量（URL、路径、超时等）
- 提供目录创建和配置验证方法

### db.py
- SQLite 数据库管理
- 表结构:
  - `sdn_entries`: 实体快照（uid, name, type, programs, hash）
  - `snapshots`: 快照元数据（日期、记录数、文件hash）
  - `changes`: 变更记录（类型、实体、详情）
- 支持批量插入以提升性能

### fetcher.py
- 下载 OFAC SDN XML（约 30MB）
- 带重试机制（默认 3 次）
- 验证下载内容是否为有效 XML
- 也支持获取 Recent Actions 页面

### parser.py
- 使用 `xml.etree.ElementTree` 解析 SDN XML
- 提取实体信息: uid, 名称, 类型, 制裁项目, 地址, 备注
- 计算每条实体的内容哈希用于变更检测

### detector.py
- 比对两个快照的实体哈希表
- 检测类型: added（新增）, removed（移除）, modified（修改）
- 对修改的实体进行字段级别的 diff

### summarizer.py
- 生成 Markdown 格式的变更摘要
- 包含: 概览表、新增/移除/修改列表、制裁项目统计、类型统计
- 支持状态报告（首次运行/无变化时）

### notifier.py
- 构建飞书交互卡片（Interactive Card）
- 支持多种消息类型: 更新通知、状态报告、测试消息、错误告警
- 使用蓝色/红色模板区分消息重要程度

### main.py
- CLI 入口，支持 `run` / `test` / `status` 三个命令
- 编排完整的监控流程
- 日志双输出（文件 + 控制台）

## 变更检测算法

```
输入: 新快照实体列表 entries_new[]
      上一快照日期 prev_date

1. 加载旧实体的 hash 表 {uid: content_hash}
2. 构建新实体的 hash 表 {uid: content_hash}
3. 比较 uid 集合:
   added_uids   = new_uids - old_uids
   removed_uids = old_uids - new_uids
   modified_uids = {uid | uid 相同但 hash 不同}
4. 构建变更记录，包含详情
```

## 数据库 ER 图

```
┌──────────────┐       ┌──────────────┐       ┌──────────────┐
│  snapshots   │       │ sdn_entries  │       │   changes    │
├──────────────┤       ├──────────────┤       ├──────────────┤
│ id (PK)      │       │ uid (PK)     │       │ id (PK)      │
│ snapshot_date│──┐    │ last_name    │       │ snapshot_date│
│ publish_date │  │    │ first_name   │       │ prev_date    │
│ record_count │  │    │ sdn_type     │       │ change_type  │
│ file_hash    │  │    │ programs     │       │ uid          │
│ created_at   │  │    │ addresses    │       │ last_name    │
└──────────────┘  │    │ remarks      │       │ first_name   │
                  └───▶│ content_hash │       │ sdn_type     │
                       │ snapshot_date│◀──┐   │ programs     │
                       └──────────────┘   │   │ remarks      │
                                          │   │ detail       │
                                          │   │ created_at   │
                                          │   └──────────────┘
                                          │
                             (snapshot_date 关联)
```
