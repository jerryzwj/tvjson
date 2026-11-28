# -*- coding: utf-8 -*-
"""第二步：归一化去重 + 并发探测连通性，产出 data/probe.json。"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import new_session, probe, shorten_vod, source_key  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")


def group_entries(entries):
    groups = {}
    for e in entries:
        key = source_key(e["api"])
        g = groups.setdefault(key, {
            "key": key,
            "name": e["name"],
            "content": e["content"],
            "detail": e["detail"],
            "programs": [],
            "candidates": [],
        })
        if e["program"] not in g["programs"]:
            g["programs"].append(e["program"])
        # 既有原始长地址，也有 /vod 截断后的精简地址；精简版优先生效
        short = shorten_vod(e["api"])
        for api, is_short in ((short, True), (e["api"], False)):
            exist = [c["api"] for c in g["candidates"]]
            if api in exist:
                if is_short:  # 同一地址既能当精简版又能当原始版时，标成精简
                    for c in g["candidates"]:
                        if c["api"] == api:
                            c["short"] = True
                continue
            g["candidates"].append({"api": api, "program": e["program"], "short": is_short})
    return groups


def main():
    config = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    crawl = json.load(open(os.path.join(DATA_DIR, "crawl.json"), encoding="utf-8"))
    groups = group_entries(crawl["entries"])
    print(f"[check] 归一化后 {len(groups)} 个源（原始 {len(crawl['entries'])} 条）")

    timeout = config.get("probe_timeout", 8)
    workers = int(config.get("probe_workers", 24))
    check_search = bool(config.get("check_search", True))

    def work(item):
        gkey, api = item
        s = new_session()
        return gkey, api, probe(s, api, timeout=timeout, check_search=check_search)

    tasks = [(k, c["api"]) for k, g in groups.items() for c in g["candidates"]]
    results = {}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(work, t) for t in tasks]
        for f in as_completed(futs):
            gkey, api, res = f.result()
            results.setdefault(gkey, {})[api] = res
            done += 1
            if done % 20 == 0:
                print(f"[check] {done}/{len(tasks)}")

    out = []
    for key, g in groups.items():
        cands = []
        for c in g["candidates"]:
            r = results.get(key, {}).get(c["api"], {"ok": False, "error": "no_result"})
            r.update({"api": c["api"], "program": c["program"], "short": bool(c.get("short"))})
            cands.append(r)
        alive = [c for c in cands if c["ok"]]
        out.append({
            "key": key,
            "name": g["name"],
            "content": g["content"],
            "detail": g["detail"],
            "programs": g["programs"],
            "candidates": cands,
            "alive": bool(alive),
        })

    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, "probe.json")
    json.dump({
        "checked_at": crawl["crawled_at"],
        "total": len(out),
        "alive": sum(1 for x in out if x["alive"]),
        "keys": out,
    }, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    ok = sum(1 for x in out if x["alive"])
    print(f"[check] 完成：{ok}/{len(out)} 个源连通 -> {path}")


if __name__ == "__main__":
    main()
