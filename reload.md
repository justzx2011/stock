stock-v2 容器有几种重启方式，按平滑程度排列：

1. 最平滑 — 只重启应用进程（不重启容器）

docker exec stock-v2 supervisorctl restart stock-web
docker exec stock-v2 supervisorctl restart init_and_cron

只重启 supervisor 管理的进程，容器本身不中断，MySQL 连接不断，cron 不丢调度。适合代码修改后生效。

2. 平滑 — 重启容器

cd /Users/user/work/stock-v2
docker compose -f dev-docker-compose.yml restart stock

容器重启但镜像不重建，volume 数据保留。init_and_cron 进程会重新 sleep 120 秒再跑初始化。大约 10 秒中断。

3. 重建 — 更新 compose 配置后重启

cd /Users/user/work/stock-v2
docker compose -f dev-docker-compose.yml up -d

刚才我们加 cron 挂载时用的就是这种。如果 compose 文件没变，它只会 Recreate 有变化的容器。MySQL 容器不受影响。

4. 完全重建 — 重建镜像

cd /Users/user/work/stock-v2
docker compose -f dev-docker-compose.yml up -d --build

只有改了 Dockerfile 才需要，目前不需要。

---

因为你改的代码都在 volume 挂载目录里（/data/stock），所以方式 1 就够了——代码改动立即生效，不需要重启容器。
