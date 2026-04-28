---
name: openkb-agent-native
description: Use when Codex or Claude Code needs to build, update, query, chat over, or lint an OpenKB-compatible knowledge base in the current working directory without configuring a separate LLM API. Trigger for agent-native knowledge workflows over local documents, wiki summaries, concepts, explorations, and `.openkb` state.
---

# OpenKB Agent Native

## Overview

以 OpenKB 的知识库形态工作，但不要调用 `openkb add`、`openkb query`、`openkb chat` 这类依赖外部 LLM API 的命令。
把当前 agent 当作“知识编译器”和“问答器”，把本 skill 自带脚本当作确定性工具层。

默认假设：

- 当前工作目录就是知识库根目录
- 如果当前目录还不是 KB，先初始化
- 兼容目录结构优先于复刻原始 OpenKB 内部实现

## 独立性边界

这版 skill 的脚本层已经不再依赖 `OpenKB/openkb/` 目录，可以单独拷走使用。

但它仍然依赖 Python 环境中的第三方包，至少包括：

- `PyYAML`
- `pymupdf`
- `markitdown`

也就是说：

- 它现在已经独立于 `OpenKB` 主项目源码
- 但还不是“零依赖、拷过去就能裸跑”的单文件工具

## 独立使用准备

如果要把这个 skill 单独带到别的机器上，至少准备：

1. Python 3.10+
2. 安装依赖：

```powershell
python -m pip install -r requirements.txt
```

如果你希望保留更多文件格式支持，优先安装 `requirements.txt` 里的完整依赖，而不是自行删减。

## Quick Start

### 初始化知识库

```powershell
python .\scripts\init_kb.py .
```

### 扫描 `raw/` 中待处理文件

```powershell
python .\scripts\sync_raw.py .
```

### 转换单个文件到 `wiki/sources/`

```powershell
python .\scripts\convert_source.py <source-path> --kb-dir .
```

### 重建索引

```powershell
python .\scripts\rebuild_index.py .
```

### 查看状态

```powershell
python .\scripts\status.py .
```

### 运行结构 lint

```powershell
python .\scripts\lint_structural.py .
```

## 工作流

### `init`

当用户要求“初始化知识库”“新建知识库”“把当前目录变成 OpenKB 风格知识库”时：

1. 运行 `scripts/init_kb.py`
2. 确认生成了：
   - `.openkb/config.yaml`
   - `.openkb/hashes.json`
   - `raw/`
   - `wiki/sources/`
   - `wiki/summaries/`
   - `wiki/concepts/`
   - `wiki/explorations/`
   - `wiki/reports/`
   - `wiki/AGENTS.md`
   - `wiki/index.md`
   - `wiki/log.md`

### `add` / `update`

当用户要求“新增文档”“导入目录”“更新知识库”“同步 raw 中的新文件”时：

1. 若文件还不在 `raw/`，先复制或调用 `scripts/convert_source.py`
2. 若处理的是 `raw/` 目录批量更新，先运行 `scripts/sync_raw.py` 获取待处理清单
3. 对每个待处理文件：
   - 转换到 `wiki/sources/`
   - 阅读转换后的 source
   - 生成或更新对应 `wiki/summaries/<name>.md`
   - 优先更新已有 `wiki/concepts/*.md`，避免制造近义概念页
4. 成功写完 summary / concept 后，再更新 `.openkb/hashes.json`
5. 运行 `scripts/rebuild_index.py`
6. 向 `wiki/log.md` 追加 `ingest` 或 `update` 记录

重要规则：

- 不要在 summary / concept 还没写完时就提前登记哈希状态
- 除非在做底层修复，否则不要手工改 `wiki/sources/`
- 更新时优先“重写受影响页面”，而不是追加互相冲突的内容

### `query`

当用户要求“查询知识库”“回答知识库里的问题”“保存查询结果”时，按这个顺序检索：

1. `wiki/index.md`
2. 相关 `wiki/concepts/*.md`
3. 相关 `wiki/summaries/*.md`
4. 证据不足时再回看 `wiki/sources/*`

如果用户要求保存结果：

- 写入 `wiki/explorations/<slug>.md`
- 运行 `scripts/rebuild_index.py`
- 向 `wiki/log.md` 追加 `query` 记录

### `chat`

当用户要求“围绕知识库持续对话”时：

- 当前 agent 线程本身就是主聊天界面
- `.openkb/chats/*.json` 只是用于跨线程或跨运行恢复会话
- 需要恢复某个 KB 对话时，先读取 chat store，再在当前线程续聊

### `lint`

当用户要求“检查知识库质量”“找断链”“找重复概念”“检查一致性”时：

1. 先运行 `scripts/lint_structural.py`
2. 再由 agent 做语义层检查：
   - concept 是否重复
   - 定义是否冲突
   - 结论是否缺证据
   - 页面是否失焦
   - summary 与 concept 是否互相矛盾
3. 把结构层和语义层结果合并写入 `wiki/reports/lint_YYYYMMDD_HHMMSS.md`
4. 向 `wiki/log.md` 追加 `lint` 记录

### `watch`

第一版不要把 `watch` 理解成完全自治的后台流水线。

推荐语义：

- `sync` 是主要支持路径
- 先扫描 `raw/` 变化
- 再由当前 agent 在前台执行转换、摘要、概念更新和索引重建

这代表：

- 可以“持续更新”
- 但不是“脱离当前 agent 自动思考并后台编译”

## 写作规则

### Summary

- 一页对应一个文档
- 如果目标 KB 已经在 summary 中使用 frontmatter，就继续沿用
- 优先给出一句核心摘要，再展开关键结论
- 文档过长时，先分块读 source，再综合

### Concept

- 一页只聚焦一个主题
- 先判断现有 concept 是否可吸收新内容，再决定是否创建新页
- 必须使用 `[[wikilinks]]` 连接相关 summary / concept
- 如果该 KB 已有 `brief` frontmatter，就继续保留

### Exploration

- Exploration 是“值得保留的查询结果”，不是每次问答都必须生成
- 适合保存分析、比较、阶段结论、专题整理

## 脚本地图

- `scripts/init_kb.py`
  初始化兼容知识库骨架和默认文件。
- `scripts/hash_registry.py`
  维护 `.openkb/hashes.json` 的读写和文件哈希计算。
- `scripts/convert_source.py`
  将源文件转换到 `wiki/sources/`，不负责编译 summary / concept。
- `scripts/sync_raw.py`
  扫描 `raw/` 中新增或变更文件，返回待处理清单。
- `scripts/rebuild_index.py`
  根据 summaries、concepts、explorations 重建 `wiki/index.md`。
- `scripts/status.py`
  统计知识库的 sources、summaries、concepts、reports、raw 和总索引数。
- `scripts/lint_structural.py`
  执行结构层检查，例如断链、缺 source、hash 指向缺失 raw。
- `scripts/chat_store.py`
  保存和恢复 KB 对话会话元数据。

## 长文档策略

第一版不依赖 PageIndex Cloud，也不承诺完整复刻树检索。

长文档处理原则：

1. 先尽可能本地转换
2. 必要时分页或分块读取
3. 先生成阶段性摘要
4. 再产出最终 summary / concept
5. query 时按需回看具体 source 片段

## 何时读取 references

- 需要确认目录和状态文件约定时：读 `references/kb-layout.md`
- 需要模仿 summary / concept / exploration 页面样式时：读 `references/page-patterns.md`
- 需要看典型用户请求对应什么动作时：读 `references/workflow-examples.md`

## 常见错误

- 直接调用 `openkb add/query/chat`，结果又回到外部 API 依赖链
- 还没写完 summary / concept 就把 hash 先登记，导致状态和页面不同步
- 每次导入都新建 concept，造成概念页膨胀
- 把 `watch` 当成后台自动推理服务
- 把 `wiki/sources/` 当成普通笔记区直接手工改写

## 结束前检查

- 当前目录是否真的是 KB 根目录
- `wiki/index.md` 是否已重建
- `wiki/log.md` 是否追加了本次操作
- `.openkb/hashes.json` 是否只登记成功处理完的文档
- 如执行了 lint，是否输出到了 `wiki/reports/`
