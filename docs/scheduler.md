# 本地调度器（带前端）

提供一个极简的 Web 前端，控制当前项目 `main.py` 的定时运行：

- 预设频率：每 1 小时、每 30 分钟
- 自定义频率：任意分钟数（>=1）
- 控制：开始/更新定时、停止定时、立即运行一次
- 状态：展示下次运行时间、上次开始/结束时间、退出码与是否在运行
- 日志：`/log` 路由查看尾部日志，完整日志输出在仓库根目录 `scheduler.log`

## 安装依赖

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 启动调度器

```bash
python scheduler_app.py
# 默认 http://127.0.0.1:5001

# 自定义端口
SCHEDULER_PORT=5100 python scheduler_app.py
```

打开浏览器访问本地地址，选择频率后点击“开始/更新定时”。

> 提示：调度器通过 `sys.executable main.py` 运行当前仓库的入口，不修改原有逻辑，`max_instances=1` 保证不重入，`coalesce=True` 避免堆积。

