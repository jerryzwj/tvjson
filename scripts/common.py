# -*- coding: utf-8 -*-
"""公共工具：HTTP、URL 归一化、接口探测。"""
import json
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

SECOND_LEVEL_SUFFIX = {
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn",
    "com.hk", "com.tw", "co.uk", "com.au",
}

_FORMAT_TAIL = re.compile(r"/at/(xml|xmlsea|json|josn|m3u8|txt)/?$", re.I)


def _detect_proxy():
    """优先环境变量；Windows 上回落读取系统代理（Clash 等本地端口）。"""
    import os
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        v = os.environ.get(name)
        if v:
            return v
    if os.name != "nt":
        return None
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
        server, _ = winreg.QueryValueEx(key, "ProxyServer")
        if not enabled or not server:
            return None
        if "=" in server:
            parts = dict(p.split("=", 1) for p in server.split(";") if "=" in p)
            server = parts.get("https") or parts.get("http") or ""
        return f"http://{server}" if server else None
    except Exception:
        return None


def new_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "application/json,text/xml,application/xml,*/*",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
    })
    proxy = _detect_proxy()
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def get_text(session, url, timeout=8):
    """返回 (text, status, elapsed_ms)，失败抛异常由调用方处理。"""
    t0 = time.monotonic()
    r = session.get(url, timeout=timeout, verify=False, allow_redirects=True)
    ms = int((time.monotonic() - t0) * 1000)
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text, r.status_code, ms


def root_domain(host):
    host = (host or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    if ".".join(parts[-2:]) in SECOND_LEVEL_SUFFIX:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def clean_url(raw):
    """清掉 URL 里的空白字符和写坏的协议头（如 'https: //'、'https//'、'https,//'）。"""
    u = re.sub(r"\s+", "", raw or "")
    if not u:
        return ""
    u = u.replace(",//", "://")
    u = re.sub(r"(?i)^(https|http)\s*:?\s*/{0,3}", lambda m: m.group(1).lower() + "://", u)
    return u


def shorten_vod(api_url):
    """把 /vod 之后的字段（at/xml、at/xmlsea、from/xxx/... 等）全部截掉。"""
    p = urlsplit(api_url.strip())
    m = re.search(r"/vod", p.path or "", re.I)
    if not m:
        return api_url.strip()
    path = (p.path or "")[:m.end()] + "/"
    return urlunsplit((p.scheme, p.netloc, path, p.query, ""))


def source_key(api_url):
    """去重 key：同域名收敛；带 /vod 的接口一律视为该域的一个源。"""
    p = urlsplit(clean_url(api_url))
    host = root_domain(p.hostname or "")
    if re.search(r"/vod", p.path or "", re.I):
        return f"{host}#vod"
    path = re.sub(r"(/provide/art)/?$", r"\1/", p.path or "/")
    if not path.endswith("/"):
        path += "/"
    return f"{host}{path}"


def build_query_url(api_url, **params):
    """在接口地址上叠加查询参数，去掉原有的 ac 等参数。"""
    p = urlsplit(clean_url(api_url))
    q = dict(parse_qsl(p.query))
    q.update({k: v for k, v in params.items() if v is not None})
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), ""))


_BAD_MARKERS = (
    "cloudflare", "just a moment", "cf-challenge", "attention required",
    "域名已过期", "站点已关闭", "该站点已被关闭", "404 not found",
    "access denied", "forbidden",
)


def looks_blocked(text, status):
    if status == 200:
        low = text[:1200].lower()
        if any(m in low for m in _BAD_MARKERS):
            return True
        stripped = text.lstrip()
        return not (stripped.startswith("{") or stripped.startswith("<"))
    return True


def parse_items(text):
    """把 ac=list 的返回解析成 [(vod_id, vod_name), ...]。支持 JSON 与 XML。"""
    items = []
    text = text.strip()
    if text.startswith("{"):
        data = json.loads(text)
        for row in (data.get("list") or []):
            if not isinstance(row, dict):
                continue
            vid = row.get("vod_id") or row.get("id") or ""
            name = str(row.get("vod_name") or row.get("name") or "").strip()
            if name:
                items.append((str(vid), name))
        return items
    if text.startswith("<"):
        for m in re.finditer(r"<video[^>]*>(.*?)</video>", text, re.S | re.I):
            body = m.group(1)
            nm = re.search(r"<name[^>]*>(.*?)</name>", body, re.S | re.I)
            if not nm:
                continue
            name = nm.group(1)
            name = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", name, flags=re.S)
            name = re.sub(r"<[^>]+>", "", name).strip()
            if name:
                im = re.search(r"<id[^>]*>(.*?)</id>", body, re.S | re.I)
                items.append((im.group(1).strip() if im else "", name))
        return items
    return items


_ADULT_MARKERS = (
    "强奸", "乱伦", "淫", "色情", "成人都市", "人妻", "自拍", "偷拍",
    "裸", "av", "ntr", "无码", "流出", "三级", "巨乳", "空姐", "学生",
)


def probe(session, api_url, timeout=8, check_search=False):
    """探测单个接口。返回 dict：ok / ms / count / fingerprint / search / adult / error。"""
    res = {"ok": False, "ms": None, "count": 0, "fingerprint": None,
           "search": None, "adult": False, "error": ""}
    try:
        text, status, ms = get_text(session, build_query_url(api_url, ac="list"), timeout)
        res["ms"] = ms
        low = text.lower()
        res["adult"] = any(k in low for k in _ADULT_MARKERS)
        if looks_blocked(text, status):
            res["error"] = f"blocked/http{status}"
            return res
        items = parse_items(text)
        res["count"] = len(items)
        if len(items) < 5:
            res["error"] = f"too_few_items:{len(items)}"
            return res
        names = sorted(n for _, n in items[:60])
        import hashlib
        res["fingerprint"] = hashlib.sha1("|".join(names).encode("utf-8")).hexdigest()[:16]
        res["ok"] = True
        if check_search:
            try:
                stext, sstatus, _ = get_text(
                    session, build_query_url(api_url, ac="detail", wd="你好"), timeout)
                if not looks_blocked(stext, sstatus):
                    res["search"] = len(parse_items(stext)) > 0
                else:
                    res["search"] = False
            except Exception:
                res["search"] = False
    except Exception as e:  # noqa: BLE001
        res["error"] = type(e).__name__
    return res
