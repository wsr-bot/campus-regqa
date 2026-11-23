# 校园教务规章问答 Agent

> 把散落在几十份教务文件里的规定，做成一个**答案可追溯、算分不靠模型、查不到就说查不到**的问答服务。

学生问「重修不及格的课成绩怎么记」，得到的不是一段通顺但没出处的话，而是：

> 重修成绩按实际考核成绩记载，并在成绩单上标注「重修」字样。**[1]**
>
> 依据：《XX大学学生学籍管理规定（2021年修订）》第十二条 · 2021 年生效

---

## 为什么值得做

教务规定有三个特点，决定了通用聊天模型在这里不可靠：

| 问题 | 通用模型的表现 | 本项目的处理 |
|---|---|---|
| 规定散在几十份文件里，还有新旧版本 | 记住的是训练语料里的旧版本，答得很自信 | 只从现行版检索，废止条款不进索引 |
| 学分、绩点、选课冲突是算出来的 | 口算，错一位就是事故 | 全部走工具，代码算完回填 |
| 文件里确实没写的情况很多 | 编一个听起来合理的答案 | 明确回「未查到」，并指到具体窗口 |

---

## 核心特性

**1. 引用是可验证的，不是装饰**
答案里每条事实性结论后必须紧跟 `[[chunk_id]]` 标记，服务端把它解析成可点开的条款卡片。任何一条结论找不到对应条款，`verify` 节点直接拒答——**生成节点无权宣布自己答对了**。

**2. 算分与清单由代码完成**
学分缺口、绩点、选课冲突、材料清单、办理窗口，5 个能力封装成纯函数工具，模型只能调用、不能代算。

**3. 拒答是一条正常路径，不是失败**
检索最高分低于阈值、引用为空、条款不存在，都会走拒答分支，并给出办理窗口与咨询去处。**宁可不答，不给错答案。**

**4. 工具可被外部复用**
5 个工具经 FastMCP 暴露为独立 MCP Server，编排逻辑与业务能力分离，其他客户端可直接调用同一套实现。

---

## 架构

```
       ┌──────────┐
问题 → │  route   │ 规则优先，未命中交 fast 档模型分类
       └────┬─────┘
            ▼
       ┌──────────┐  按 file_type 圈范围 → 向量召回 20 → 单文件限 3 块
       │ retrieve │
       └────┬─────┘
            ▼
       ┌──────────┐  向量前 3 强制入围 + 其余批量打分补 2 → 5 块进上下文
       │  rerank  │
       └────┬─────┘
            ▼
       ┌──────────┐  strong 档，绑定 5 个工具
       │  agent   │ ⇄ ┌───────┐
       └────┬─────┘   │ tools │ 纯函数，参数经 pydantic 校验
            ▼         └───────┘
       ┌──────────┐  [[chunk_id]] → 引用卡片
       │ compose  │
       └────┬─────┘
            ▼
       ┌──────────┐  引用数、条款存在性、拒答互斥，全用代码判定
       │  verify  │
       └────┬─────┘
            ▼
         answer + citations
```

编排用 LangGraph 静态图，7 个节点 1 条回环。流程固定，所以不引入动态规划——静态图可断点、可回放、好调试。

---

## 规模与指标

| 项 | 数值 |
|---|---|
| 语料 | 40+ 份教务文件（学籍、考试、选课、毕业审核、证明办理、培养方案） |
| 知识块 | 约 1,800 个条款级块，带「文件名 / 章节号 / 生效年份」三标签 |
| 工具 | 5 个（学分绩点、选课冲突、材料清单、办理窗口、结果导出） |
| 评测集 | 200 条标注样本，含「文件里确实没写」的拒答类 |
| 条款级召回命中率 | 68% → **94%** |
| 引用准确率 | 76% → **93%** |
| 该拒答却硬答 | 18% → **3%** |

命中率的提升来自三处改动：按「章—条」切块（而不是固定长度）、按 `file_type` 先圈范围再召回、以及向量前 3 名强制入围不参与重排淘汰。

---

## 快速开始

### Docker（推荐）

```bash
docker build -t campus-regqa .
docker run -p 8000:8000 \
  -e DASHSCOPE_API_KEY=sk-xxx \
  -e DEEPSEEK_API_KEY=sk-xxx \
  campus-regqa
```

打开 http://127.0.0.1:8000

### 本地

```bash
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -r requirements.txt
python scripts/check_env.py                      # 自检依赖与密钥
python scripts/init_db.py                        # 建库与目录
python -m app.main                               # 启动
```

### 导入教务文件

1. 把 PDF 放进 `data/raw/`，OCR 结果存为同名的 `.txt`
2. 在 `data/registry/manifest.yaml` 里登记（标题、文号、生效年份、是否替代旧版）
3. 先干跑看切块质量，再正式入库：

```bash
python -m app.ingest.run_ingest --doc-id xueji_2021 --dry-run   # 只报告，不写库不花钱
python -m app.ingest.run_ingest --all                            # 正式入库
```

`--dry-run` 会打印目录对齐情况、切出多少条、可疑断块清单。**先看报告再入库**，切歪了改文件比清库重来便宜。

> 本仓库不包含校内文件本体（受版权与授权限制），需自行放入。

---

## 配置

全部参数集中在 `config/app.yaml`，代码内不出现魔法数字。

| 段 | 关键项 |
|---|---|
| `llm.tiers.strong` | `claude → deepseek → qwen` 降级链，任一家不可用自动切下一家 |
| `llm.tiers.fast` | `qwen → deepseek`，用于改写与重排打分 |
| `embedding` | `dashscope text-embedding-v3`，1024 维，带落库缓存 |
| `retrieval` | `top_k=20`、`rerank_keep=5`、`force_keep_topn=3`、`per_file_cap=3`、`min_score=0.55` |
| `budget` | `max_turns=4`、单轮 15s 墙钟、各节点超时 |

密钥只从环境变量读取，不进配置文件：`DASHSCOPE_API_KEY`、`DEEPSEEK_API_KEY`、`ANTHROPIC_API_KEY`。

---

## 目录结构

```
app/
├── main.py            装配与启动自检
├── api/               chat(SSE) · documents · export · health
├── llm/               providers(两家协议统一) · router(档位与降级)
├── graph/
│   ├── build.py       图装配
│   └── nodes/         route · retrieve · rerank · agent · tools_node · compose · verify
├── tools/             schemas(单一真相源) · impl(5 个纯函数) · data_access
├── mcp/server.py      5 个工具的 MCP 暴露
├── ingest/            extract · outline · chunker · manifest · indexer
├── retrieval/         embed(带缓存) · store(Chroma)
├── store/             db(WAL) · repo(纯 SQL) · cache
└── web/               单页前端
data/
├── raw/               PDF 与 OCR sidecar
├── registry/          文件登记表
├── structured/        材料 / 窗口 / 学分要求
└── chroma/            向量索引
```

依赖方向单向：`api → graph → tools|retrieval|llm → store`。`tools/impl.py` 不 import `graph` 与 `llm`，保证工具能脱离 Agent 独立单测。

---

## 关键取舍

**切块按「章—条」，不按固定长度**
要报「第几条」，块边界就必须等于条边界。超长条按款二次切，`part_no` 递增，引用仍指向同一条。

**SQLite 为正本，Chroma 只是索引**
向量库丢了能重建，条款原文不能。`is_current` 由 `supersedes` 链推导而非手填——手填一定填错。

**Redis 不用**
单实例内存 LRU 够用。`cache.py` 留了 `CacheBackend` 接口，多实例时换实现即可。

**流式按节点推，不按字推**
意图、召回、重排、工具调用都是真实执行进度，前端能看见系统在想什么。正文按块推送，体验与逐字流一致。

---

## 接口

| 方法 / 路径 | 说明 |
|---|---|
| `POST /api/chat` | 一次性返回答案与引用 |
| `POST /api/chat/stream` | SSE 流式：`route` `retrieval` `rerank` `tool_call` `token` `citation` `done` |
| `GET /api/documents` | 已收录文件清单与解析状态 |
| `GET /api/exports/{file}` | 下载导出的答案（文件名白名单校验） |
| `GET /api/health` | 知识库规模、向量数、各档模型密钥状态 |

---

## 评测

200 条标注样本，每条标出标准答案、应当引用的条款、以及「文件里确实没写」的拒答类。

| 指标 | 判定方式 |
|---|---|
| 引用准确率 | 程序判定：答案引用的 `chunk_id` 是否属于标注的应当引用集合 |
| 拒答正确率 | 程序判定：该拒答的是否拒答，不该拒答的是否给了答案 |
| 答案正确率 | 独立模型裁判（与生成不同供应商）+ 人工抽检 20% |

程序能判的一律用程序判，只有「答案说得对不对」才交给裁判模型，且裁判看不到生成时的推理过程。

---

## 边界

- 只收录公开教务文件，不涉及任何需要登录或授权的内容
- 回答不构成官方解释，最终以教务处口径为准
- 单进程设计，未做多用户隔离；公网部署前需自行加访问控制
- 扫描件不做自动 OCR：没有文字层的文件会被标记，需人工提供文本后入库

---

## License

MIT
