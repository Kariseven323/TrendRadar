Flask 快速开始要点：
- 创建 app：`app = Flask(__name__)`
- 路由：`@app.route('/', methods=['GET','POST'])`
- 使用模板 `render_template` 与 `url_for('static', filename='...')`
- 运行：`flask --app app run` 或 `app.run()`

APScheduler 关键要点：
- Web 场景下用 `BackgroundScheduler()` 运行后台调度
- `add_job(func, 'interval', minutes=30, id='job', max_instances=1, coalesce=True, misfire_grace_time=60)`
- `scheduler.start()` 后可通过 `get_job('job')` 查看 `next_run_time`；`remove_job('job')` 停止
- 建议仅在 `if __name__ == '__main__':` 中启动调度，避免导入即启动