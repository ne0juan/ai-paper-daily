# AI 风向 · Daily AI Bot

> 每天 10 分钟，看懂 AI 圈的人在想什么、行业在往哪走。在线阅读：**https://dailyaibot.com**

一个全自动的中文 AI 资讯站：每天北京时间 **08:00 / 13:00 / 19:00** 抓取、筛选、翻译、存档，只收**今天和昨天（T / T-1）**发布的内容。

- **今日风向**：大模型把当天内容归纳成 3–5 条趋势判断，每条附出处
- **资讯与观点**：量子位、极客公园、IT 之家等中文媒体（约 7 成）+ Sam Altman、Dario Amodei、Karpathy、Paul Graham、宝玉等 30 余位业内人物的博客 + OpenAI / Anthropic / DeepMind / DeepSeek / xAI 官方发布
- **热门项目**：GitHub 上正在走红的 AI 开源项目（Skills、MCP、智能体框架…），附上手提示
- **架构 · 评测**：模型架构、智能体 harness、评测方法与新基准（科学空间、Lilian Weng、Sebastian Raschka、METR、arXiv…）
- **论文**：每天只留讨论度最高的 1–2 篇，附白话解读和中文全文链接
- **原文存档**：论文 PDF / 文章渲染成 PDF 托管在站内，国内网络也能打开

零服务器：GitHub Actions 定时运行，Cloudflare Pages 免费托管，大模型默认用 DeepSeek（每天成本约几毛钱）。

```
GitHub Actions (cron ×3/天)
  ├─ radar/sources.py   RSS / 列表页 / HF Daily Papers / Hacker News / GitHub Trending / arXiv 检索
  ├─ radar/scoring.py   规则分：来源权威度 40% + 热度 30% + 多源交叉 12% + 机构 8% + 新鲜度 10%
  ├─ radar/llm.py       大模型审稿：质量 1-10、中文标题/观点提炼/白话解读/上手提示、「今日风向」
  ├─ radar/mirror.py    原文存档：论文下载 PDF，文章用无头 Chromium 渲染成 PDF
  ├─ data/              每日精选 JSON（提交回仓库，天然就是历史档案）
  └─ scripts/build_site.py → dist/ → Cloudflare Pages（wrangler）→ scripts/smoke.py 线上冒烟测试
```

## 部署你自己的一份

1. Fork 本仓库，在 Actions 页启用工作流。
2. 在 Settings → Secrets and variables → Actions 添加：
   - `CLOUDFLARE_API_TOKEN`（必需）：Cloudflare API Token，权限含 *Account · Cloudflare Pages · Edit*。
   - `LLM_API_KEY`（推荐）：DeepSeek 等 OpenAI 兼容接口的 Key；不填则退回规则打分 + 免费机器翻译。
3. Actions → *Radar scan & deploy* → Run workflow。首次运行会自动创建 Pages 项目并部署到 `<项目名>.pages.dev`。
4. 数据源、每期篇数、各栏目配额、时效规则都在 `config/sources.yaml`。
5. 绑定自己的域名：Actions → *Bind custom domain*，再在域名 DNS 处把 `@` 和 `www` CNAME 到 `<项目名>.pages.dev`。

## 大模型

默认走 DeepSeek `deepseek-chat`（OpenAI 兼容接口）。要换模型：在仓库 Variables 里设 `LLM_PROVIDER` / `LLM_BASE_URL` / `LLM_MODEL`（Anthropic、OpenAI、Kimi、通义等均可）。
没有 Key 时自动退回：规则打分 + 免费机器翻译（标「机翻」）。

## 其他可选配置

- `X_BEARER_TOKEN`（Secret）：接入 X 官方 API，抓取 `config/sources.yaml` 里账号推文中的论文/文章链接。
- `CLOUDFLARE_ACCOUNT_ID`（Secret）：不填则用 Token 自动发现。
- `PAGES_PROJECT`（Variable）：Pages 项目名，默认 `ai-paper-radar`。
- 数据源、每期篇数、阈值、PDF 保留天数：改 `config/sources.yaml`。
- 只让特定人访问：Cloudflare Zero Trust → Access 给域名加邮箱规则。

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
- **今日必读**：全天最值得读的 3 条（不含专栏和论文）。
- **晚间刊 → 午间刊 → 晨间刊**：从新到旧，同一条只出现一次；上次访问后新增的标「新」。
- **热门项目 → 架构 · 评测**：两个专栏各有独立配额，不挤占资讯名额。
- **论文**：每天只留讨论度最高的 1–2 篇，附白话解读和「中文全文」（幻觉翻译）。
- 观点类卡片以人物为先（姓名｜身份）；「读原文」默认打开本站存档 PDF。

## 设计质检

`scripts/design_qa.py` 以端传媒、FT中文网、少数派的正文排版与 W3C《中文排版需求》为标准，自动测量：正文 ≥16px、行高 1.7–1.95、每行 28–42 字、中文不斜体、中英文间留空、对比度（正文 ≥7:1、辅助 ≥4.5:1，含暗色）、字号阶梯 ≤8、手机触控 ≥44px、首屏可见头条、无横向滚动、卡片信息不重复。CI 中不达标即失败；每次部署后对线上站点再测一次，报告在 `reports` 分支。

## 许可

代码以 [MIT](LICENSE) 协议开源。站点收录的文章、论文及存档 PDF 版权归原作者所有，仅供学习交流；如有侵权请提 Issue，会尽快移除。

欢迎提 Issue / PR 推荐数据源（尤其是中文一线从业者的博客），候选源放在 `config/candidates.yaml`，每周由 *Probe sources* 工作流自动体检。
