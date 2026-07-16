#!/bin/sh

export PYTHONIOENCODING=utf-8
export LANG=zh_CN.UTF-8
export PYTHONPATH=/data/stock
export LC_CTYPE=zh_CN.UTF-8

mkdir -p /data/logs/tensorflow



DATE=`date +%Y-%m-%d:%H:%M:%S`

echo $DATE >> /data/logs/run_init.log

echo "wait 120 second , mysqldb is starting ." >> /data/logs/run_init.log
sleep 120

/usr/local/bin/python3 /data/stock/jobs/basic_job.py  >> /data/logs/run_init.log

# https://stackoverflow.com/questions/27771781/how-can-i-access-docker-set-environment-variables-from-a-cron-job
# 解决环境变量输出问题。
printenv | grep -v "no_proxy" >> /etc/environment

# 第一次后台执行日数据。
nohup bash /data/stock/jobs/cron.daily/run_daily &

# 注册定时任务（幂等：先剔除旧的晨报/尾盘/魅视科技/华盛昌风格条目再追加，避免容器重启后重复堆积）
chmod +x /data/stock/jobs/cron.9h/run_morning_report
chmod +x /data/stock/jobs/cron.14h/run_evening_report
chmod +x /data/stock/jobs/cron.8h30/run_meishi_tech
chmod +x /data/stock/jobs/cron.8h30/run_hsc_style
CRONTAB_TMP=$(mktemp)
crontab -l 2>/dev/null | grep -v -F "run_morning_report" | grep -v -F "run_evening_report" | grep -v -F "run_meishi_tech" | grep -v -F "run_hsc_style" > "$CRONTAB_TMP"
printf '30 8 * * 1-5 bash /data/stock/jobs/cron.8h30/run_meishi_tech\n' >> "$CRONTAB_TMP"
printf '35 8 * * 1-5 bash /data/stock/jobs/cron.8h30/run_hsc_style\n' >> "$CRONTAB_TMP"
printf '28 9 * * 1-5 bash /data/stock/jobs/cron.9h/run_morning_report\n' >> "$CRONTAB_TMP"
printf '0 17 * * 1-5 bash /data/stock/jobs/cron.14h/run_evening_report\n' >> "$CRONTAB_TMP"
crontab "$CRONTAB_TMP"
rm -f "$CRONTAB_TMP"

#启动cron服务。在前台
/usr/sbin/cron -f