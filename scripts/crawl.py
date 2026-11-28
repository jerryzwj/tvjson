# -*- coding: utf-8 -*-
"""第一步：抓取 yszzq 采集接口目录，产出 data/crawl.json。"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import clean_url, get_text, new_session  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")


def strip_tags(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">"))
    return re.sub(r"\s+", " ", s).strip()


def list_pages(base, max_pages, session, timeout):
    """逐页抓列表页 href；JSON-LD 里的条目一并收进（列表页只给部分）。"""
    urls = []
    base = base.rstrip("/") + "/"
    for idx in range(1, max_pages + 1):
        page = base if idx == 1 else base + f"index_{idx}.html"
        try:
            html, status, _ = get_text(session, page, timeout)
        except Exception:
            break
        if status != 200:
            break

        before = len(urls)
        m = re.search(r'<script type="application/ld\+json">\s*(\{.*?"ItemList".*?\})\s*</script>',
                      html, re.S)
        if m:
            try:
                data = json.loads(m.group(1))
                for it in data.get("itemListElement", []):
                    u = it.get("url")
                    if u and u not in urls:
                        urls.append(u)
            except Exception:
                pass
        for u in re.findall(r'href="(https://www\.yszzq\.com/ziyuan/api/\d+\.html)"', html):
            if u not in urls:
                urls.append(u)

        print(f"[crawl] 列表页 {idx}: +{len(urls) - before} 条，累计 {len(urls)}")
        if len(urls) == before:  # 没有新链接，说明到末尾了
            break
        time.sleep(0.3)
    return urls


def sanitize_api(raw):
    """丢掉下载包、占位模板、本地地址等脏值，返回清洗并截断后的接口地址。"""
    u = clean_url(raw)
    if not re.match(r"^https?://[A-Za-z0-9]", u):
        return None
    low = u.lower()
    if re.search(r"\.(zip|rar|7z|exe|apk|txt)$", low):
        return None
    if any(k in low for k in ("localhost", "127.0.0.1", "0.0.0.0", "example.com")):
        return None
    if any(ch in u for ch in "{}()（）"):
        return None
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    p = urlsplit(u)
    host = (p.hostname or "")
    if "." not in host:
        return None
    q = [(k, v) for k, v in parse_qsl(p.query) if k.lower() != "ac"]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), ""))


def parse_detail(html):
    title = ""
    mt = re.search(r'<meta property="og:title" content="([^"]*)"', html)
    if mt:
        title = mt.group(1).strip()
    content = ""
    mc = re.search(r"接口内容：</td><td>([\s\S]*?)</td>", html)
    if mc:
        content = strip_tags(mc.group(1))
    fmt = ""
    mf = re.search(r"接口格式：</td><td>([\s\S]*?)</td>", html)
    if mf:
        fmt = strip_tags(mf.group(1))

    rows = []
    start = html.find("采集接口地址")
    if start < 0:
        return title, content, fmt, rows
    end = html.find("</section>", start)
    seg = html[start:end] if end > start else html[start:]
    for tr in re.finditer(
            r"<tr>\s*<td width=\"20%\">([\s\S]*?)</td>\s*<td width=\"70%\">([\s\S]*?)</td>"
            r"\s*<td width=\"10%\">([\s\S]*?)</td>", seg):
        prog = strip_tags(tr.group(1))
        api = sanitize_api(strip_tags(tr.group(2)))
        if not api:
            continue
        rows.append({"program": prog or "其他", "api": api, "content": content, "format": fmt})
    return title, content, fmt, rows


def main():
    config = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    base = config["source"]
    session = new_session()
    timeout = config.get("crawl_timeout", 15)
    include_novel = bool(config.get("include_novel", False))

    links = list_pages(base, config.get("max_pages", 6), session, timeout)
    print(f"[crawl] 详情页 {len(links)} 个")
    if not links:
        raise SystemExit("[crawl] 未取到任何详情页链接")

    entries = []
    for i, url in enumerate(links, 1):
        try:
            html, status, _ = get_text(session, url, timeout)
            if status != 200:
                print(f"[crawl] skip {url} http{status}")
                continue
        except Exception as e:  # noqa: BLE001
            print(f"[crawl] fail {url} {type(e).__name__}")
            continue
        title, content, fmt, rows = parse_detail(html)
        if not title:
            continue
        for row in rows:
            if not include_novel and "/provide/art/" in row["api"]:
                continue
            entries.append({
                "name": title.rstrip("，, "),
                "program": row["program"],
                "api": row["api"],
                "content": content,
                "format": fmt,
                "detail": url,
            })
        time.sleep(0.15)
        if i % 10 == 0:
            print(f"[crawl] {i}/{len(links)} 条记录 {len(entries)}")

    os.makedirs(DATA_DIR, exist_ok=True)
    out = os.path.join(DATA_DIR, "crawl.json")
    json.dump({
        "crawled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": base,
        "detail_pages": len(links),
        "entries": entries,
    }, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    sites = len({e["name"] for e in entries})
    print(f"[crawl] 完成：{sites} 个站，{len(entries)} 条记录 -> {out}")


if __name__ == "__main__":
    main()
