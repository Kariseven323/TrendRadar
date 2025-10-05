import os
import sys
import threading
import subprocess
from datetime import datetime
from typing import Optional, Dict, Any

import pytz
from flask import Flask, render_template, request, redirect, url_for, jsonify
from apscheduler.schedulers.background import BackgroundScheduler


APP_TZ = pytz.timezone("Asia/Shanghai")
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(PROJECT_ROOT, "scheduler.log")
JOB_ID = "trendradar_job"


class RunState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running: bool = False
        self.last_start: Optional[datetime] = None
        self.last_end: Optional[datetime] = None
        self.last_returncode: Optional[int] = None
        self.interval_minutes: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "running": self.running,
            "last_start": self._fmt(self.last_start),
            "last_end": self._fmt(self.last_end),
            "last_returncode": self.last_returncode,
            "interval_minutes": self.interval_minutes,
        }

    @staticmethod
    def _fmt(dt: Optional[datetime]) -> Optional[str]:
        if not dt:
            return None
        # 统一展示为北京时间
        return dt.astimezone(APP_TZ).strftime("%Y-%m-%d %H:%M:%S")


run_state = RunState()
# 统一为北京时间，确保 next_run_time 与页面展示一致
scheduler = BackgroundScheduler(timezone=APP_TZ)


def ensure_log_dir() -> None:
    log_dir = os.path.dirname(LOG_FILE)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)


def run_trendradar() -> None:
    """执行当前项目 `main.py`。串行执行，避免重入。"""
    with run_state.lock:
        if run_state.running:
            # 已在运行，直接返回，避免重入
            return
        run_state.running = True
        run_state.last_start = datetime.now(tz=APP_TZ)

    ensure_log_dir()
    # 使用当前解释器运行，继承当前环境变量
    cmd = [sys.executable, os.path.join(PROJECT_ROOT, "main.py")]

    # 追加写日志，方便排查
    with open(LOG_FILE, "ab") as log_fp:
        log_fp.write(b"\n=== Run at %s ===\n" % run_state.last_start.strftime("%Y-%m-%d %H:%M:%S").encode())
        log_fp.flush()
        try:
            completed = subprocess.run(
                cmd,
                cwd=PROJECT_ROOT,
                stdout=log_fp,
                stderr=log_fp,
                check=False,
            )
            returncode = completed.returncode
        except Exception as e:
            log_fp.write(f"[scheduler] run error: {e}\n".encode("utf-8", errors="ignore"))
            returncode = -1
        finally:
            end = datetime.now(tz=APP_TZ)
            log_fp.write(b"=== End ===\n")

    with run_state.lock:
        run_state.running = False
        run_state.last_end = end
        run_state.last_returncode = returncode


def schedule_job(minutes: int) -> None:
    """以固定分钟间隔调度任务。"""
    global scheduler
    if minutes <= 0:
        minutes = 1

    # 替换或新增任务
    # 常规定时：首次执行为“当前时刻 + 间隔”（不强制立刻运行）
    scheduler.add_job(
        run_trendradar,
        trigger="interval",
        minutes=minutes,
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        replace_existing=True,
    )
    with run_state.lock:
        run_state.interval_minutes = minutes


def remove_job() -> None:
    job = scheduler.get_job(JOB_ID)
    if job:
        scheduler.remove_job(JOB_ID)
    with run_state.lock:
        run_state.interval_minutes = None


def create_app() -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
        job = scheduler.get_job(JOB_ID)
        next_run = job.next_run_time if job else None
        next_run_str = (
            next_run.astimezone(APP_TZ).strftime("%Y-%m-%d %H:%M:%S") if next_run else None
        )
        state = run_state.to_dict()
        return render_template(
            "scheduler.html",
            next_run=next_run_str,
            state=state,
        )

    @app.route("/schedule", methods=["POST"])
    def schedule_route():
        preset = request.form.get("preset", "60")
        minutes = 60
        if preset == "60":
            minutes = 60
        elif preset == "30":
            minutes = 30
        elif preset == "custom":
            try:
                minutes = int(request.form.get("custom_minutes", "60"))
            except Exception:
                minutes = 60
            if minutes <= 0:
                minutes = 1

        schedule_job(minutes)
        # 可选：保存并立即运行
        run_now = request.form.get("run_immediately") == "1"
        if run_now:
            with run_state.lock:
                already_running = run_state.running
            if not already_running:
                t = threading.Thread(target=run_trendradar, daemon=True)
                t.start()
        return redirect(url_for("index"))

    @app.route("/stop", methods=["POST"])
    def stop_route():
        remove_job()
        return redirect(url_for("index"))

    @app.route("/run-once", methods=["POST"])
    def run_once_route():
        with run_state.lock:
            if run_state.running:
                return redirect(url_for("index"))
        t = threading.Thread(target=run_trendradar, daemon=True)
        t.start()
        return redirect(url_for("index"))

    @app.route("/status")
    def status():
        job = scheduler.get_job(JOB_ID)
        next_run = job.next_run_time if job else None
        return jsonify(
            {
                "job": bool(job),
                "next_run": next_run.astimezone(APP_TZ).strftime("%Y-%m-%d %H:%M:%S")
                if next_run
                else None,
                "state": run_state.to_dict(),
            }
        )

    @app.route("/log")
    def tail_log():
        # 简单返回日志末尾内容（最多约 2000 行字符）
        if not os.path.exists(LOG_FILE):
            return "暂无日志", 200, {"Content-Type": "text/plain; charset=utf-8"}
        try:
            with open(LOG_FILE, "rb") as fp:
                data = fp.read()
            text = data.decode("utf-8", errors="ignore")
            # 仅返回最后 4000 字符，避免过大
            return (
                text[-4000:]
                if len(text) > 4000
                else text
            ), 200, {"Content-Type": "text/plain; charset=utf-8"}
        except Exception as e:
            return f"读取日志失败: {e}", 500, {"Content-Type": "text/plain; charset=utf-8"}

    return app


if __name__ == "__main__":
    # 启动调度器 + Flask 本地服务（仅本机）
    scheduler.start()
    app = create_app()
    # 允许通过环境变量覆盖端口
    port = int(os.environ.get("SCHEDULER_PORT", "5001"))
    app.run(host="127.0.0.1", port=port, debug=False)
