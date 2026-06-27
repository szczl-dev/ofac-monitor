# OFAC 监控系统 - 部署指南

## macOS 部署

### 方式一：crontab 定时任务（推荐）

```bash
# 1. 确保 run.sh 有执行权限
chmod +x run.sh

# 2. 编辑 crontab
crontab -e

# 3. 添加定时任务（每天早上 9:00 执行）
0 9 * * * /Users/littlecuteh/Desktop/hw-project/ofac-monitor/run.sh

# 4. 查看已设置的定时任务
crontab -l
```

### 方式二：launchd（macOS 原生）

创建 `~/Library/LaunchAgents/com.ofac.monitor.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ofac.monitor</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/littlecuteh/Desktop/hw-project/ofac-monitor/run.sh</string>
    </array>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>/Users/littlecuteh/Desktop/hw-project/ofac-monitor/logs/launchd_out.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/littlecuteh/Desktop/hw-project/ofac-monitor/logs/launchd_err.log</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

加载 launchd 任务:

```bash
# 加载
launchctl load ~/Library/LaunchAgents/com.ofac.monitor.plist

# 查看状态
launchctl list | grep com.ofac.monitor

# 卸载
launchctl unload ~/Library/LaunchAgents/com.ofac.monitor.plist
```

## Linux 部署

### systemd 定时器

创建 `/etc/systemd/system/ofac-monitor.service`:

```ini
[Unit]
Description=OFAC Sanctions List Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=your-username
WorkingDirectory=/path/to/ofac-monitor
ExecStart=/usr/bin/python3 -m src.main scrape
StandardOutput=append:/path/to/ofac-monitor/logs/systemd.log
StandardError=append:/path/to/ofac-monitor/logs/systemd_error.log
```

创建 `/etc/systemd/system/ofac-monitor.timer`:

```ini
[Unit]
Description=OFAC Monitor Daily Timer
Requires=ofac-monitor.service

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

激活:

```bash
sudo systemctl daemon-reload
sudo systemctl enable ofac-monitor.timer
sudo systemctl start ofac-monitor.timer
sudo systemctl status ofac-monitor.timer
```

## 日志查看

```bash
# 查看今日日志
tail -f logs/monitor.log

# 查看 cron 执行日志
tail -f logs/cron_$(date +%Y%m%d).log

# 查看最近 50 行
tail -50 logs/monitor.log

# 搜索错误
grep ERROR logs/monitor.log
```

## 数据维护

```bash
# 查看数据库状态
python3 -m src.main status

# 数据库位置
ls -lh data/ofac_monitor.db

# 清理旧 XML 文件（保留最近 30 天）
find data/current -name "sdn_*.xml" -mtime +30 -delete

# 备份数据库
cp data/ofac_monitor.db data/ofac_monitor_backup_$(date +%Y%m%d).db
```

## 故障排查

### 1. 飞书收不到消息
- 确认 Webhook URL 正确: `cat .env | grep FEISHU`
- 测试推送: `python3 -m src.main test`
- 检查飞书机器人是否被移除

### 2. 下载失败
- 检查网络连接
- 确认能访问 `https://www.treasury.gov`
- 查看日志: `tail -50 logs/monitor.log`

### 3. cron 不执行
- 确认 cron 语法正确: `crontab -l`
- 检查绝对路径是否正确
- 确认 `run.sh` 有执行权限: `chmod +x run.sh`
- 查看系统日志: `mail`（cron 会发邮件到本地）

### 4. Python 模块找不到
- 确保在项目根目录执行
- 使用 `python3 -m src.main` 而非直接运行 `.py` 文件
