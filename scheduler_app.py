import os
import sys
import threading
import subprocess
from datetime import datetime
from typing import Optional, Dict, Any, List

import pytz
from flask import Flask, render_template, request, redirect, url_for, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger


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
        self.schedule_mode: Optional[str] = None  # 'interval' | 'times'
        self.time_points: Optional[List[str]] = None  # ['08:00','12:30']

    def to_dict(self) -> Dict[str, Any]:
        return {
            "running": self.running,
            "last_start": self._fmt(self.last_start),
            "last_end": self._fmt(self.last_end),
            "last_returncode": self.last_returncode,
            "interval_minutes": self.interval_minutes,
            "schedule_mode": self.schedule_mode,
            "time_points": self.time_points,
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


def _remove_all_jobs() -> None:
    """移除当前应用创建的所有作业（按 ID 前缀匹配）。"""
    jobs = scheduler.get_jobs()
    for job in jobs:
        if job.id == JOB_ID or job.id.startswith(f"{JOB_ID}_"):
            scheduler.remove_job(job.id)


def schedule_interval(minutes: int) -> None:
    """以固定分钟间隔调度任务。"""
    if minutes <= 0:
        minutes = 1

    _remove_all_jobs()
    # 首次执行为“当前时刻 + 间隔”（不强制立刻运行）
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
        run_state.schedule_mode = "interval"
        run_state.time_points = None


def schedule_times(times: List[str]) -> None:
    """按每日固定时刻列表调度任务。

    - times 形如 ["08:00", "12:30"， ...]
    - 若列表为空，则默认每小时整点（00 分）
    """
    _remove_all_jobs()

    parsed: List[str] = []
    for t in times:
        t = t.strip()
        if not t:
            continue
        s = (
            t.replace("点", "")
             .replace("－", "-")
             .replace("—", "-")
             .replace("–", "-")
             .strip()
        )

        # 解析小时范围：如 "6-21" 或 "06-21"（表示每整点）
        if "-" in s and ":" not in s:
            try:
                sh_str, eh_str = s.split("-", 1)
                sh = int(sh_str)
                eh = int(eh_str)
            except Exception:
                sh = eh = -1
            if 0 <= sh <= 23 and 0 <= eh <= 23 and sh <= eh:
                for h in range(sh, eh + 1):
                    parsed.append(f"{h:02d}:00")
                continue

        # 解析具体时刻：HH:MM
        parts = s.split(":")
        if len(parts) == 2:
            try:
                h = int(parts[0])
                m = int(parts[1])
            except ValueError:
                continue
            if 0 <= h <= 23 and 0 <= m <= 59:
                parsed.append(f"{h:02d}:{m:02d}")

    # 若无有效时刻，默认每小时整点
    if not parsed:
        job = scheduler.add_job(
            run_trendradar,
            trigger=CronTrigger(minute=0, second=0, timezone=APP_TZ),
            id=f"{JOB_ID}_hourly",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
            replace_existing=True,
        )
        created = [job.id]
        effective_points = ["每小时整点"]
    else:
        created = []
        effective_points = []
        # 为避免 hour/minute 笛卡尔积，逐个时刻创建单独 cron 任务
        for idx, tp in enumerate(sorted(set(parsed))):
            h, m = [int(x) for x in tp.split(":")]
            job = scheduler.add_job(
                run_trendradar,
                trigger=CronTrigger(hour=h, minute=m, second=0, timezone=APP_TZ),
                id=f"{JOB_ID}_{idx}",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=300,
                replace_existing=True,
            )
            created.append(job.id)
            effective_points.append(tp)

    with run_state.lock:
        run_state.interval_minutes = None
        run_state.schedule_mode = "times"
        run_state.time_points = effective_points


def remove_job() -> None:
    _remove_all_jobs()
    with run_state.lock:
        run_state.interval_minutes = None
        run_state.schedule_mode = None
        run_state.time_points = None


def _earliest_next_run_time() -> Optional[datetime]:
    """获取当前应用作业中最近的下次运行时间。"""
    next_times = []
    for job in scheduler.get_jobs():
        if job.id == JOB_ID or job.id.startswith(f"{JOB_ID}_"):
            if job.next_run_time:
                next_times.append(job.next_run_time)
    if not next_times:
        return None
    return min(next_times)


def create_app() -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
        next_run = _earliest_next_run_time()
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
        mode = request.form.get("mode", "times")  # 默认固定时刻

        if mode == "interval":
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
            schedule_interval(minutes)
        else:
            # 固定时刻，逗号分隔 HH:MM 列表；留空=每小时整点
            tp_raw = request.form.get("time_points", "").strip()
            times = [x.strip() for x in tp_raw.split(",") if x.strip()] if tp_raw else []
            schedule_times(times)

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
        next_run = _earliest_next_run_time()
        return jsonify(
            {
                "job": any(
                    (job.id == JOB_ID or job.id.startswith(f"{JOB_ID}_"))
                    for job in scheduler.get_jobs()
                ),
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
    # 默认：若未配置任何任务，则按“每小时整点”运行
    if not any((job.id == JOB_ID or job.id.startswith(f"{JOB_ID}_")) for job in scheduler.get_jobs()):
        schedule_times([])
    app = create_app()
    # 允许通过环境变量覆盖端口
    port = int(os.environ.get("SCHEDULER_PORT", "5001"))
    app.run(host="127.0.0.1", port=port, debug=False)
