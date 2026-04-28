# Page Patterns

## Summary Pages

Recommended pattern:

```md
---
doc_type: short
full_text: sources/example.md
---

# Example Document

## 核心摘要
一到两段讲清楚这份文档最重要的内容。

## 主要结论
- 关键结论 1
- 关键结论 2

## 与知识库的关系
- 关联到哪些 `[[concepts/...]]`
```

## Concept Pages

If the KB already uses frontmatter, keep it:

```md
---
sources: [summaries/example.md]
brief: 一句话说明这个概念页讲什么
---

# 概念名称

## 概念定义
给出稳定定义。

## 关键观察
- 观察 1
- 观察 2

## 与其他概念的关系
- [[concepts/related-a]]
- [[concepts/related-b]]
```

## Exploration Pages

Use for saved query outputs that are worth keeping:

```md
# 问题标题

## 问题
原始提问

## 结论
直接回答

## 证据
- 来自 `[[summaries/...]]`
- 来自 `[[concepts/...]]`
```

## Index Page

Keep three sections:

- `## Documents`
- `## Concepts`
- `## Explorations`

Use `[[wikilinks]]` consistently.
