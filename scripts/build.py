# -*- coding: utf-8 -*-
"""第三步：指纹去重 + 状态合并 + 输出 caiji-tvbox / report.md / README 状态块。"""
import datetime as dt
import json
import os
import re
import sys
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import root_domain, source_key  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
TZ = dt.timezone(dt.timedelta(hours=8))
NOWDT = dt.datetime.now(TZ)
TODAY = NOWDT.strftime("%Y-%m-%d")
NOW = NOWDT.strftime("%Y-%m-%d %H:%M CST")

STATUS_ICON = {"ok": "✅", "fail": "❌", "removed": "⚠️", "mirror": "🔁", "gone": "🕳️"}


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    return json.load(open(path, encoding="utf-8"))


def score(cand, search_ok):
    """选哪条地址：https > /vod 截断后的精简地址 > 能搜索 > 延迟低。"""
    return (
        1 if str(cand.get("api", "")).startswith("https://") else 0,
        1 if cand.get("short") else 0,
        1 if search_ok else 0,
        -(cand.get("ms") or 9999),
    )


def rekey_state(state):
    """把历史 state 里的 key 迁移到当前归一化规则，避免规则升级后出现重复条目。"""
    merged = {}
    for key, st in (state.get("sources") or {}).items():
        api = st.get("api")
        nk = source_key(api) if api else key
        if nk not in merged:
            merged[nk] = st
            continue
        cur = merged[nk]
        cur["ok_count"] = cur.get("ok_count", 0) + st.get("ok_count", 0)
        cur["fail_count"] = cur.get("fail_count", 0) + st.get("fail_count", 0)
        cur["fail_streak"] = max(cur.get("fail_streak", 0), st.get("fail_streak", 0))
        cur["history"] = (cur.get("history") or []) + (st.get("history") or [])
        if not cur.get("search") and st.get("search"):
            cur["search"] = st["search"]
    state["sources"] = merged


def main():
    config = load_json(os.path.join(ROOT, "config.json"), {})
    probe_data = load_json(os.path.join(DATA_DIR, "probe.json"))
    if probe_data is None:
        raise SystemExit("[build] 缺少 data/probe.json，请先运行 check.py")

    state = load_json(os.path.join(ROOT, "state.json"), {"sources": {}})
    threshold = int(config.get("drop_after_failures", 3))
    history_keep = int(config.get("history_keep", 100))
    out_format = config.get("output_format", "api_site")
    exclude_adult = bool(config.get("exclude_adult", True))
    rekey_state(state)

    seen_keys = set()
    for item in probe_data["keys"]:
        key = item["key"]
        seen_keys.add(key)
        st = state["sources"].setdefault(key, {
            "name": item["name"], "detail": item["detail"],
            "first_seen": TODAY, "ok_count": 0, "fail_count": 0,
            "fail_streak": 0, "history": [],
        })
        st["name"] = item["name"]
        st["detail"] = item["detail"]
        st["content"] = item["content"]
        st["programs"] = item["programs"]
        st["listed"] = True

        alive = [c for c in item["candidates"] if c["ok"]]
        best = None
        if alive:
            best = sorted(
                alive,
                key=lambda c: score(c, c.get("search") is True),
                reverse=True,
            )[0]
            st.update({
                "api": best["api"],
                "latency_ms": best.get("ms"),
                "fingerprint": best.get("fingerprint"),
                "search": best.get("search"),
                "adult": bool(best.get("adult")),
                "last_ok": TODAY,
            })
        else:
            if not st.get("api") and item["candidates"]:
                st["api"] = item["candidates"][0]["api"]
            errs = [c.get("error", "") for c in item["candidates"]]
            st["last_error"] = errs[0] if errs else "unknown"

        st["last_check"] = TODAY

        # 历史按自然日去重：同一天多次运行只算一次，避免连击导致误剔除
        hist = {h["d"]: h for h in (st.get("history") or []) if h.get("d")}
        first_today = TODAY not in hist
        hist[TODAY] = {
            "d": TODAY,
            "ok": bool(alive),
            "ms": (best or {}).get("ms"),
            "err": "" if alive else st.get("last_error", ""),
        }
        recs = [hist[d] for d in sorted(hist)][-history_keep:]
        st["history"] = recs
        if first_today:
            if alive:
                st["ok_count"] = st.get("ok_count", 0) + 1
            else:
                st["fail_count"] = st.get("fail_count", 0) + 1

        # 连续失败天数按自然日算，而不是按运行次数
        streak = 0
        for h in reversed(recs):
            if h.get("ok"):
                break
            streak += 1
        st["fail_streak"] = streak

    # 目录里已消失、但历史上还活着的源，保留最近 7 天的记录
    for key, st in state["sources"].items():
        if key in seen_keys:
            continue
        st["listed"] = False
        last_ok = st.get("last_ok")
        if last_ok:
            days = (dt.date.fromisoformat(TODAY) - dt.date.fromisoformat(last_ok)).days
            st["gone"] = days > 7

    # ---- 选源：先排除已剔除/成人/已下线，再按内容指纹去重 ----
    pool = []
    adult_skipped = 0
    for key, st in state["sources"].items():
        streak = st.get("fail_streak", 0)
        if streak >= threshold:
            continue
        if not st.get("api"):
            continue
        if st.get("gone"):
            continue
        if not st.get("listed") and not st.get("last_ok"):
            continue
        if exclude_adult and st.get("adult"):
            adult_skipped += 1
            continue
        pool.append((key, st))

    # 只有今天连通（fail_streak == 0）才进入输出文件
    candidates = [(k, s) for k, s in pool if s.get("fail_streak", 0) == 0]

    by_fp = {}
    for key, st in candidates:
        fp = st.get("fingerprint") or f"nofp:{key}"
        dom = root_domain(urlsplit(st["api"]).hostname or "")
        by_fp.setdefault(fp, {}).setdefault(dom, []).append((key, st))

    final, mirrors = [], []
    for fp, domains in by_fp.items():
        if len(domains) == 1:
            final.extend(next(iter(domains.values())))
            continue
        # 同一片库的多个域名：只保留评分最高的那个域，域内所有播放通道都留下
        keep_dom = max(
            domains.items(),
            key=lambda kv: max(score(s, s.get("search") is True) for _, s in kv[1]),
        )[0]
        final.extend(domains[keep_dom])
        for dom, items in domains.items():
            if dom == keep_dom:
                continue
            for k, s in items:
                s["mirror_of"] = keep_dom
                mirrors.append((k, s))

    final.sort(key=lambda x: (x[1].get("content") or "", -x[1].get("ok_count", 0), x[1]["name"]))

    # ---- 输出 caiji-tvbox ----
    def channel_tag(api):
        """同一站的多个通道，用播放器 / 输出格式做区分后缀。"""
        m = re.search(r"/from/([A-Za-z0-9]+)/", api)
        if m:
            return m.group(1)
        m = re.search(r"/at/([A-Za-z0-9]+)", api)
        if m:
            return {"xmlsea": "sea", "josn": "json", "json": "json"}.get(m.group(1).lower(), m.group(1).lower())
        return ""

    _NAME_SUFFIXES = ("采集接口大全", "采集接口地址", "采集规则地址", "采集接口",
                      "采集地址", "采集网", "接口大全", "采集")

    def clean_name(name):
        n = name.strip().rstrip("，, ")
        changed = True
        while changed:
            changed = False
            for suf in _NAME_SUFFIXES:
                if n.endswith(suf) and len(n) > len(suf) + 1:
                    n = n[:-len(suf)].rstrip("，, ")
                    changed = True
        return n or name

    used = set()
    counters = {}
    shown = []
    for key, st in final:
        base = clean_name(st["name"])
        name = base
        if name in used:
            tag = channel_tag(st["api"])
            cand = f"{base}·{tag}" if tag else ""
            if cand and cand not in used:
                name = cand
        while name in used:
            counters[base] = counters.get(base, 1) + 1
            name = f"{base}·{counters[base]}"
        counters.setdefault(base, 1)
        used.add(name)
        shown.append((key, st, name))

    if out_format == "urls":
        payload = {"urls": [
            {"url": st["api"], "name": name} for _, st, name in shown]}
    elif out_format == "api_site":
        api_site = {}
        for key, st, name in shown:
            base = root_domain(st["api"].split("/")[2].split(":")[0]) if "//" in st["api"] else key.split("/")[0]
            slug = re.sub(r"[^a-z0-9_]", "_", base.replace(".", "_").replace("-", "_").lower())
            if slug in api_site:
                i = 2
                while f"{slug}_{i}" in api_site:
                    i += 1
                slug = f"{slug}_{i}"
            api_site[slug] = {
                "api": st["api"],
                "name": name,
                "detail": st.get("detail", ""),
            }
        payload = {"cache_time": 7200, "api_site": api_site}
    else:
        payload = {"sources": [
            {"name": name, "url": st["api"]} for _, st, name in shown]}

    with open(os.path.join(ROOT, "caiji-tvbox"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    state["updated"] = NOW
    with open(os.path.join(ROOT, "state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)

    # ---- 报告 ----
    total = len(state["sources"])
    out_count = len(final)
    ok_today = sum(1 for _, st in pool if st.get("fail_streak", 0) == 0)
    fail_today = sum(1 for _, st in pool if st.get("fail_streak", 0) > 0)
    removed = sum(1 for st in state["sources"].values()
                  if st.get("fail_streak", 0) >= threshold)

    def rate(st):
        tot = st.get("ok_count", 0) + st.get("fail_count", 0)
        return f"{st.get('ok_count', 0) / tot * 100:.1f}%" if tot else "-"

    def trend(st):
        hist = {h["d"]: h["ok"] for h in st.get("history", [])}
        days = [(dt.date.fromisoformat(TODAY) - dt.timedelta(days=i)).isoformat()
                for i in range(6, -1, -1)]
        return " ".join("✅" if hist.get(d) else ("❌" if d in hist else "·") for d in days)

    def row(key, st, icon=None):
        mark = icon or ("✅" if st.get("fail_streak", 0) == 0 else "❌")
        search = "✅" if st.get("search") is True else ("❌" if st.get("search") is False else "-")
        detail = f"[Link]({st['detail']})" if st.get("detail") else "-"
        return (f"| {mark} | {st['name']} | {detail} | "
                f"[API]({st['api']}) | {search} | {st.get('ok_count', 0)} | "
                f"{st.get('fail_count', 0)} | {rate(st)} | {trend(st)} |")

    lines = [
        "# API 健康报告（每日自动采集 + 连通检测）",
        "",
        f"最近更新：{NOW}　|　数据来源：[yszzq 采集接口目录]({config.get('source', '')})",
        "",
        f"- 采集到的源：{total}",
        f"- 输出到 `caiji-tvbox`：{out_count}",
        f"- 今日连通：{ok_today}",
        f"- 今日不通（观察中）：{fail_today}",
        f"- 连续失败已剔除：{removed}",
        f"- 内容重复合并：{len(mirrors)}",
        f"- 成人内容源已跳过：{adult_skipped}",
        "",
        "| 状态 | 资源名称 | 地址 | API | 搜索功能 | 成功次数 | 失败次数 | 成功率 | 最近7天趋势 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for key, st in final:
        lines.append(row(key, st, "✅" if st.get("fail_streak", 0) == 0 else "⚠️"))
    for key, st in mirrors:
        lines.append(row(key, st, "🔁"))
    for key, st in state["sources"].items():
        if key in {k for k, _ in final} or key in {k for k, _ in mirrors}:
            continue
        lines.append(row(key, st, "⚠️" if st.get("fail_streak", 0) < threshold else "❌"))

    report = "\n".join(lines) + "\n"
    with open(os.path.join(ROOT, "report.md"), "w", encoding="utf-8") as f:
        f.write(report)

    # ---- README 状态块 ----
    summary = "\n".join(lines[3:12]) + "\n"
    readme_path = os.path.join(ROOT, "README.md")
    if os.path.exists(readme_path):
        text = open(readme_path, encoding="utf-8").read()
        if "<!-- STATUS:START -->" in text and "<!-- STATUS:END -->" in text:
            head, rest = text.split("<!-- STATUS:START -->", 1)
            _, tail = rest.split("<!-- STATUS:END -->", 1)
            text = (head + "<!-- STATUS:START -->\n" + summary +
                    "<!-- STATUS:END -->" + tail)
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(text)
            print("[build] README 状态块已更新")

    print(f"[build] 输出 {out_count} 个源（合并重复 {len(mirrors)}），剔除 {removed} 个连续失败源")


if __name__ == "__main__":
    main()
