初始化与运行：
- python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
- python main.py
- docker compose -f docker/docker-compose.yml up -d
- docker compose -f docker/docker-compose-build.yml up --build -d

查看输出：
- 打开 index.html 或 output/<日期>/html/当日汇总.html

CI 验证：
- 手动触发 GitHub Actions：Hot News Crawler

注意：配置在 config/config.yaml 与 config/frequency_words.txt，敏感配置经环境变量/Secrets 提供。