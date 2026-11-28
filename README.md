# tvjson · caiji-tvbox

每天自动抓取公开的影视采集接口目录，去重、连通检测后输出 `caiji-tvbox`，并生成健康报告。

## 订阅地址

```
https://raw.githubusercontent.com/jerryzwj/tvjson/main/caiji-tvbox
```

健康报告：[report.md](report.md)

## 当前状态

<!-- STATUS:START -->

- 采集到的源：68
- 输出到 `caiji-tvbox`：42
- 今日连通：49
- 今日不通（观察中）：19
- 连续失败已剔除：0
- 内容重复合并：7
- 成人内容源已跳过：0

<!-- STATUS:END -->

## 它做了什么

```
yszzq 采集接口目录 (3 页列表 → 逐个详情页)
        │
        ├─ crawl.py   抓取：资源站名 / CMS程序 / 接口地址 / 资源内容
        │
        ├─ check.py   归一化去重 → 并发探测 ?ac=list（JSON/XML 双协议）
        │              判定连通：HTTP 200 + 非拦截页 + 能解析出 ≥5 条记录
        │              同时算内容指纹 + 测搜索接口
        │
        └─ build.py   指纹跨域名合并 → 合并 state 历史 → 输出 caiji-tvbox / report.md
```

## 去重怎么做的（三层）

| 层级 | 依据 | 解决的问题 |
|---|---|---|
| 1. URL 归一化 | 根域 + 路径，`/at/xml`、`/at/xmlsea`、`/at/josn` 视为同一接口 | 综合接口 / 苹果cms / 海洋cms / 飞飞cms 共用同一地址的重复 |
| 2. 内容指纹 | `sha1(排序后的前 60 个片名)` | 换域名的镜像站（红牛、茅台等 `caiji.*` / `www.*` 系列） |
| 3. 状态历史 | `state.json` 记录 fail_streak | 避免单次网络抖动误删好源，连续 3 天不通才剔除 |

同一指纹下保留哪条：`https` 优先 → 搜索可用优先 → 平均延迟更低优先。

## 配置

`config.json`：

| 字段 | 说明 |
|---|---|
| `source` | 采集目录地址 |
| `max_pages` | 最多翻几页列表 |
| `probe_timeout` | 单次探测超时（秒） |
| `probe_workers` | 并发探测线程数 |
| `check_search` | 是否测搜索接口 |
| `drop_after_failures` | 连续失败几次后剔除（默认 3） |
| `output_format` | `api_site`（默认，MoonTV/LunaTV 风格）或 `urls`（`{"urls":[{"url","name"}]}`） |

## 本地运行

```bash
pip install -r requirements.txt
python scripts/pipeline.py
```

产物：`caiji-tvbox`、`report.md`、`state.json`、`README.md` 状态块。

## 说明

- 接口均为第三方站点公开提供的采集地址，可用性与内容合规性由对应站点负责，请自行核验。
- GitHub Actions 对 60 天无提交的仓库会自动停用定时任务，保持仓库活跃即可。
