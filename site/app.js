/* Paper Radar — MVP front-end. Vanilla JS, no dependencies, same-origin requests only.
   Reading logic: 今日必读 (top 3 of the whole day) → newest issue first (晚间 → 午间 → 晨间),
   items added since your last visit are marked 新. */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (u) => (/^(https?:\/\/|pdf\/)/i.test(u || "") ? u : "#");
  const ls = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  };

  const SLOT_ZH = { morning: "晨间刊", noon: "午间刊", evening: "晚间刊" };
  const NEWEST_FIRST = ["evening", "noon", "morning"];
  const WEEK = "日一二三四五六";
  const MUST_READ = 3;

  const S = { index: null, days: new Map(), date: null, lastVisit: Number(ls.get("pr-last-visit")) || 0 };

  async function getJSON(url) {
    const r = await fetch(url, { cache: "no-cache" });
    if (!r.ok) throw new Error(`${r.status} ${url}`);
    return r.json();
  }
  async function loadDay(date) {
    if (!S.days.has(date)) S.days.set(date, (await getJSON(`data/days/${date}.json`)).items || []);
    return S.days.get(date);
  }

  function fmtDate(d) {
    const [y, m, dd] = d.split("-").map(Number);   // calendar date in Beijing; independent of viewer's timezone
    const wd = new Date(Date.UTC(y, m - 1, dd)).getUTCDay();
    return { md: `${m}/${dd}`, full: `${y}年${m}月${dd}日`, wk: `周${WEEK[wd]}` };
  }
  function ago(iso) {
    if (!iso) return "—";
    const m = Math.round((Date.now() - new Date(iso)) / 60000);
    if (m < 1) return "刚刚";
    if (m < 60) return `${m} 分钟前`;
    if (m < 1440) return `${Math.round(m / 60)} 小时前`;
    return `${Math.round(m / 1440)} 天前`;
  }
  const hash = (s) => { let h = 2166136261; for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return (h >>> 0) / 4294967295; };
  const isNew = (it) => S.lastVisit && Date.parse(it.selected_at || 0) > S.lastVisit;

  function pdfLink(it) {
    if (it.mirror_pdf) return { url: it.mirror_pdf, label: it.mirror_kind === "snapshot" ? "读原文 PDF" : "读 PDF" };
    if (it.pdf_url) return { url: it.pdf_url, label: "读 PDF（原站）" };
    return { url: it.url, label: "读原文" };
  }

  // ------------------------------------------------------------ header
  function renderHeader(items) {
    const idx = S.index.days.findIndex((d) => d.date === S.date);
    const f = fmtDate(S.date);
    const latest = NEWEST_FIRST.find((s) => items.some((i) => i.slot === s));
    $("#issue-line").textContent = `第 ${S.index.days.length - idx} 期 · ${f.full} ${f.wk}${latest ? " · 已出" + SLOT_ZH[latest] : ""}`;
    const isToday = idx === 0;
    const fresh = items.filter(isNew).length;
    $("#since").innerHTML = !isToday
      ? `往期 · 共 ${items.length} 篇`
      : !S.lastVisit
        ? `今天已精选 <b>${items.length}</b> 篇 · 每天 08:00 / 13:00 / 19:00 更新`
        : fresh
          ? `你上次来之后新增 <b>${fresh}</b> 篇，已标 <span class="new">新</span>`
          : `上次来之后没有新内容 · 下次更新：${nextScan()}`;
  }
  function nextScan() {
    const h = Number(new Date().toLocaleString("en-US", { timeZone: "Asia/Shanghai", hour: "numeric", hour12: false }));
    return h < 8 ? "今天 08:00" : h < 13 ? "今天 13:00" : h < 19 ? "今天 19:00" : "明天 08:00";
  }

  function renderDays() {
    const days = S.index.days.slice(0, 30);
    $("#days").innerHTML = days.map((d, k) => {
      const f = fmtDate(d.date);
      return `<button class="day" role="tab" data-date="${esc(d.date)}" aria-selected="${d.date === S.date}">
        <b>${k === 0 ? "今天" : f.md}</b>${f.wk} · ${d.count} 篇</button>`;
    }).join("");
    $(".day[aria-selected=true]")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  // ------------------------------------------------------------ radar
  function renderRadar(items) {
    $("#blips").innerHTML = items.map((it) => {
      const a = hash(it.id) * 2 * Math.PI;
      const r = 14 + (1 - Math.min(1, it.score || 0)) * 125;
      return `<g class="blip ${it.kind === "article" ? "article" : ""}" data-id="${esc(it.id)}" transform="translate(${(r * Math.cos(a)).toFixed(1)} ${(r * Math.sin(a)).toFixed(1)})">
        <circle class="halo" r="3"/><circle class="b" r="${(3 + (it.score || 0) * 3).toFixed(1)}"/></g>`;
    }).join("");
    clearInterval(renderRadar.timer);
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const blips = $$("#blips .blip").map((g) => {
      const m = /translate\(([-\d.]+) ([-\d.]+)\)/.exec(g.getAttribute("transform"));
      return { g, ang: (Math.atan2(-m[2], +m[1]) + 2 * Math.PI) % (2 * Math.PI) };
    });
    const t0 = performance.now();
    let prev = 0;
    renderRadar.timer = setInterval(() => {   // light blips as the sweep line passes (6s/turn, CCW)
      const cur = (((performance.now() - t0) / 6000) * 2 * Math.PI) % (2 * Math.PI);
      blips.forEach(({ g, ang }) => {
        if (prev <= cur ? ang > prev && ang <= cur : ang > prev || ang <= cur) {
          g.classList.remove("lit"); void g.getBBox(); g.classList.add("lit");
        }
      });
      prev = cur;
    }, 80);
  }

  // ------------------------------------------------------------ cards
  function card(it, i, big) {
    const pdf = pdfLink(it);
    const who = [...(it.orgs || []).slice(0, 2), ...(it.authors || []).slice(0, 3)].filter(Boolean);
    const signals = (it.sources || []).map((s) => `<span class="src">${esc(s.name)}${s.signal ? " " + esc(s.signal) : ""}</span>`).join("");
    const more = (it.links || []).filter((l) => l.url !== pdf.url);
    return `<article class="card ${big ? "big" : ""}" id="c-${esc(it.id)}" data-id="${esc(it.id)}" style="--i:${i}">
      <div class="meta">
        ${isNew(it) ? `<span class="new">新</span>` : ""}
        <span class="kind ${it.kind === "article" ? "article" : ""}">${it.kind === "article" ? "文章" : "论文"}</span>
        ${signals}
        <span class="score" title="综合得分：来源权威度 · 热度 · 多源交叉${it.score_llm != null ? " · 大模型审稿" : ""}">${Math.round((it.score || 0) * 100)}</span>
      </div>
      <h3><a href="${esc(safeUrl(pdf.url))}" target="_blank" rel="noopener">${esc(it.title_zh || it.title)}</a></h3>
      ${it.title_zh ? `<p class="orig">${esc(it.title)}</p>` : ""}
      ${it.summary_zh ? `<p class="summary">${esc(it.summary_zh)}</p>` : ""}
      ${it.why_zh ? `<p class="why"><b>为什么值得读</b>${esc(it.why_zh)}</p>` : ""}
      <div class="actions">
        <a class="primary" href="${esc(safeUrl(pdf.url))}" target="_blank" rel="noopener">${esc(pdf.label)} ↗</a>
        ${more.length || who.length ? `<details class="more"><summary>更多</summary>
          ${who.length ? `<p class="who">${esc(who.join(" · "))}${(it.authors || []).length > 3 ? " 等" : ""}</p>` : ""}
          ${(it.highlights_zh || []).length ? `<ul class="hl">${it.highlights_zh.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>` : ""}
          <p class="links">${more.map((l) => `<a href="${esc(safeUrl(l.url))}" target="_blank" rel="noopener">${esc(l.label)}</a>`).join("")}</p>
        </details>` : ""}
      </div>
    </article>`;
  }

  function renderFeed(items) {
    if (!items.length) {
      $("#feed").innerHTML = `<div class="empty"><h2>雷达静默中…</h2><p>这一天还没有入选的内容。下次扫描：${nextScan()}。</p></div>`;
      return;
    }
    const byScore = [...items].sort((a, b) => b.score - a.score);
    const must = byScore.slice(0, Math.min(MUST_READ, items.length >= 6 ? MUST_READ : 1));
    const mustIds = new Set(must.map((i) => i.id));
    let html = `<section class="section must"><div class="sec-head"><h2>今日必读</h2><span>全天得分最高 · 只有 5 分钟就读这几篇</span></div>
      ${must.map((it, k) => card(it, k, true)).join("")}</section>`;
    let i = must.length;
    for (const s of NEWEST_FIRST) {
      const group = byScore.filter((it) => it.slot === s && !mustIds.has(it.id));
      if (!group.length) continue;
      const at = new Date(group[0].selected_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Shanghai" });
      html += `<section class="section" data-slot="${s}"><div class="sec-head"><h2>${SLOT_ZH[s]}</h2><span>${group.length} 篇 · ${at} 扫描</span></div>
        ${group.map((it) => card(it, i++, false)).join("")}</section>`;
    }
    $("#feed").innerHTML = html;
  }

  async function render() {
    const items = await loadDay(S.date);
    renderHeader(items);
    renderDays();
    renderFeed(items);
    renderRadar(items);
  }

  function flash(id) {
    const c = document.getElementById("c-" + id);
    if (!c) return;
    c.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
    c.classList.remove("flash"); void c.offsetWidth; c.classList.add("flash");
  }

  async function route() {
    const h = decodeURIComponent(location.hash.slice(2));
    if (h.startsWith("item/")) {
      const id = h.slice(5);
      for (const d of S.index.days.slice(0, 60)) {
        if ((await loadDay(d.date)).some((i) => i.id === id)) {
          S.date = d.date;
          await render();
          return flash(id);
        }
      }
    }
    S.date = (S.index.days.find((x) => x.date === h) || S.index.days[0]).date;
    await render();
  }

  function bind() {
    document.addEventListener("click", (e) => {
      const d = e.target.closest("[data-date]");
      if (d) { location.hash = `#/${d.dataset.date}`; return; }
      const b = e.target.closest(".blip");
      if (b) flash(b.dataset.id);
    });
    $("#theme").onclick = () => {
      const root = document.documentElement;
      const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
      root.dataset.theme = dark ? "light" : "dark";
      ls.set("pr-theme", root.dataset.theme);
    };
    window.addEventListener("hashchange", route);
    const tip = $("#radar-tip"), wrap = $(".radar-wrap");
    $("#blips").addEventListener("mouseover", (e) => {
      const b = e.target.closest(".blip"); if (!b) return;
      const it = S.days.get(S.date).find((x) => x.id === b.dataset.id);
      const r = b.getBoundingClientRect(), w = wrap.getBoundingClientRect();
      tip.textContent = it.title_zh || it.title;
      tip.style.left = `${r.left - w.left + r.width / 2}px`;
      tip.style.top = `${r.top - w.top}px`;
      tip.hidden = false;
    });
    $("#blips").addEventListener("mouseout", () => { tip.hidden = true; });
    $("#feed").addEventListener("mouseover", (e) => {
      const c = e.target.closest(".card");
      $$("#blips .blip").forEach((b) => b.classList.toggle("active", !!c && b.dataset.id === c.dataset.id));
    });
    // remember this visit once the reader has actually looked (so a quick reload doesn't clear 新)
    setTimeout(() => ls.set("pr-last-visit", String(Date.now())), 8000);
    addEventListener("pagehide", () => ls.set("pr-last-visit", String(Date.now())));
  }

  function renderColophon() {
    const r = S.index.last_run || {};
    $("#colophon").innerHTML = `
      <p>每天北京时间 08:00 / 13:00 / 19:00 自动扫描 HF Daily Papers、Hacker News、OpenAI / DeepMind / Anthropic / Meta 等实验室与专家博客，
      按来源权威度、热度、多源交叉${r.llm ? "和大模型审稿" : ""}打分，每期最多 10 篇，宁缺毋滥。</p>
      <p>「读 PDF」默认打开本站镜像，任何网络都能访问（保留约 3 周，版权归原作者，仅供内部学习）。 · <a href="feed.xml">RSS</a></p>
      <p class="dim">上次扫描 ${esc(ago(r.at))} · 候选 ${r.candidates ?? "—"} · 入选 ${r.selected ?? "—"} · 镜像 ${r.mirrored ?? "—"}</p>`;
  }

  async function boot() {
    bind();
    try { S.index = await getJSON("data/index.json"); } catch { S.index = { days: [] }; }
    renderColophon();
    if (!S.index.days?.length) {
      $("#issue-line").textContent = "创刊号筹备中";
      $("#feed").innerHTML = `<div class="empty"><h2>雷达预热中…</h2><p>首次扫描完成后，精选会出现在这里。</p></div>`;
    } else {
      await route();
    }
    document.body.dataset.ready = "1";
  }
  boot().catch((e) => {
    console.error(e);
    $("#feed").innerHTML = `<div class="empty"><h2>信号中断</h2><p>${esc(e.message)}</p></div>`;
  });
})();
