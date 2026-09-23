# AI 风向 · Daily AI Bot

每天北京时间 **08:00 / 13:00 / 19:00** 自动更新：前沿人物说了什么、AI 行业发生了什么，**中文为主（约 7 成）**；论文每天只留讨论度最高的 1–2 篇，并附白话解读。原文存档为 PDF 托管在本站，国内网络也能打开。

```
GitHub Actions (cron ×3/天)
  ├─ radar/sources.py   中文媒体（量子位/机器之心/36氪/极客公园…）· 人物博客（Altman/Karpathy/宝玉…）· 官方发布 · HN/HF 热度
  ├─ radar/scoring.py   规则分：来源权威度 40% + 热度 30% + 多源交叉 12% + 机构 8% + 新鲜度 10%
  ├─ radar/llm.py       DeepSeek 审稿：质量 1-10、中文标题/观点提炼/论文白话解读、「今日风向」趋势归纳
  ├─ radar/mirror.py    原文存档：论文下载 PDF，文章用无头 Chromium 渲染成 PDF
  ├─ data/              每日精选 JSON（提交回仓库，天然就是历史档案）
  └─ scripts/build_site.py → dist/ → Cloudflare Pages（wrangler）→ scripts/smoke.py 线上冒烟测试
```

## 大模型（DeepSeek，已配置）

Secret `LLM_API_KEY` 已设置；默认走 DeepSeek `deepseek-chat`（OpenAI 兼容接口）。要换模型：在仓库 Variables 里设 `LLM_PROVIDER` / `LLM_BASE_URL` / `LLM_MODEL`（Anthropic、OpenAI、Kimi、通义等均可）。
没有 Key 时自动退回：规则打分 + 免费机器翻译（标「机翻」）。

## 其他可选配置

- `X_BEARER_TOKEN`（Secret）：接入 X 官方 API，抓取 `config/sources.yaml` 里账号推文中的论文/文章链接。
- `CLOUDFLARE_ACCOUNT_ID`（Secret）：不填则用 Token 自动发现。
- `PAGES_PROJECT`（Variable）：Pages 项目名，默认 `ai-paper-radar`。
- 数据源、每期篇数、阈值、PDF 保留天数：改 `config/sources.yaml`。
- 国内访问更稳：在 Cloudflare Pages 项目里绑定公司子域名；只让公司员工访问：Cloudflare Zero Trust → Access 给该域名加邮箱域规则。

## 测试

```bash
pip install -r requirements.txt && python -m playwright install chromium
python -m unittest discover -s tests -t . -v
```

- `tests/test_unit.py`：解析器、去重合并、打分、LLM 输出容错（离线 fixtures）
- `tests/test_pipeline.py`：完整跑三期 → 不重复入选、论文每天 ≤2 篇且取最热 → 构建站点 → `verify_dist` 校验
- `tests/test_e2e.py`：Chromium 驱动真实页面：阅读顺序（风向→必读→三刊→论文）、不重复、「新」标记、往期切换、雷达跳转、PDF 可下载、设计质检全部通过，并截图
- `scripts/verify_dist.py`：部署前闸门（文件数/大小限制、坏 PDF、数据一致性）
- `scripts/smoke.py`：部署后线上冒烟（新版本已生效、PDF 以 application/pdf 可下载）
- `Probe sources` 工作流：每周体检所有数据源，原始响应可用于刷新 fixtures

CI 日志、截图、每次扫描报告都会发布到 `reports` 分支。

## 手动触发

Actions → *Radar scan & deploy* → Run workflow（可指定时段，或只重新部署）。

## 阅读逻辑

- **今日风向**：大模型把当天内容归纳成 3–5 条趋势判断，每条附出处。
- **今日必读**：全天最值得读的 3 条（不含论文）。
- **晚间刊 → 午间刊 → 晨间刊**：从新到旧，同一条只出现一次；上次访问后新增的标「新」。
- **论文**：每天只留讨论度最高的 1–2 篇，附白话解读和「中文全文」（幻觉翻译）。
- 观点类卡片以人物为先（姓名｜身份）；「读原文」默认打开本站存档 PDF。

## 设计质检

`scripts/design_qa.py` 以端传媒、FT中文网、少数派的正文排版与 W3C《中文排版需求》为标准，自动测量：正文 ≥16px、行高 1.7–1.95、每行 28–42 字、中文不斜体、中英文间留空、对比度（正文 ≥7:1、辅助 ≥4.5:1，含暗色）、字号阶梯 ≤8、手机触控 ≥44px、首屏可见头条、无横向滚动、卡片信息不重复。CI 中不达标即失败；每次部署后对线上站点再测一次，报告在 `reports` 分支。

镜像 PDF 版权归原作者所有，仅供内部学习交流。
