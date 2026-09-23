# Paper Radar · AI 论文雷达

每天北京时间 **08:00 / 13:00 / 19:00** 自动扫描，精选最权威、最有价值的 AI 论文与文章，并把原文**镜像成 PDF 托管在本站**，不同网络环境下都能打开。

```
GitHub Actions (cron ×3/天)
  ├─ radar/sources.py   HF Daily Papers · Hacker News · 13 个实验室/专家博客 RSS · Anthropic/Meta 列表页 · (X，可选)
  ├─ radar/scoring.py   规则分：来源权威度 40% + 热度 30% + 多源交叉 12% + 机构 8% + 新鲜度 10%
  ├─ radar/llm.py       大模型审稿（可选）：相关性、质量 1-10、中文标题/摘要/要点/标签；最终分 = 0.4 规则 + 0.6 LLM
  ├─ radar/mirror.py    论文下载原 PDF；博客文章用无头 Chromium 渲染成 PDF 快照
  ├─ data/              每日精选 JSON（提交回仓库，天然就是历史档案）
  └─ scripts/build_site.py → dist/ → Cloudflare Pages（wrangler）→ scripts/smoke.py 线上冒烟测试
```

## 启用大模型（功能位已预留，无需改代码）

仓库 Settings → Secrets and variables → Actions：

| 类型 | 名称 | 示例 |
|---|---|---|
| Secret | `LLM_API_KEY` | `sk-...` |
| Variable | `LLM_PROVIDER` | `anthropic` 或 `openai`（OpenAI 兼容接口都选这个：DeepSeek、Kimi、通义、智谱…） |
| Variable | `LLM_BASE_URL` | 例 `https://api.deepseek.com/v1`（Anthropic/OpenAI 官方可留空） |
| Variable | `LLM_MODEL` | 例 `deepseek-chat`、`claude-sonnet-4-5` |

没有 `LLM_API_KEY` 时自动退回纯规则打分，站点照常更新。每次运行最多送 40 条候选、每批 8 条（约 5 次调用）。

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
- `tests/test_pipeline.py`：完整跑两期 → 不重复入选 → 构建站点 → `verify_dist` 校验
- `tests/test_e2e.py`：Chromium 驱动真实页面：今日必读=全天最高分、时段从新到旧且不重复、「新」标记、日期切换、雷达点击跳转、PDF 可下载、手机无横向滚动、零 console 报错，并截图
- `scripts/verify_dist.py`：部署前闸门（文件数/大小限制、坏 PDF、数据一致性）
- `scripts/smoke.py`：部署后线上冒烟（新版本已生效、PDF 以 application/pdf 可下载）
- `Probe sources` 工作流：每周体检所有数据源，原始响应可用于刷新 fixtures

CI 日志、截图、每次扫描报告都会发布到 `reports` 分支。

## 手动触发

Actions → *Radar scan & deploy* → Run workflow（可指定时段，或只重新部署）。

## 阅读逻辑（MVP）

- 打开即「今天」：顶部 **今日必读** 3 篇（全天得分最高），下面按 **晚间刊 → 午间刊 → 晨间刊** 从新到旧排列，同一篇只出现一次。
- 浏览器记住上次访问时间，之后新入选的内容标 **新**，并提示「你上次来之后新增 N 篇」。
- 每张卡片：一句话讲了什么 · 为什么值得读 · 来源信号 · 一个「读 PDF」主按钮（本站镜像），其他链接收在「更多」里。
- 日期条切换往期；雷达图光点 = 当天文章，越靠近圆心得分越高，点击跳到对应卡片；明暗主题；RSS `feed.xml`。

镜像 PDF 版权归原作者所有，仅供内部学习交流。
