# Workflow Examples

## Example 1: Initialize A New KB

User request:

`把当前目录初始化成一个 OpenKB 风格知识库`

Expected flow:

1. Confirm current directory
2. Run `scripts/init_kb.py`
3. Verify `.openkb/`, `raw/`, `wiki/` were created
4. Report ready state

## Example 2: Add A Single Markdown File

User request:

`把这份会议纪要加入知识库`

Expected flow:

1. Convert with `scripts/convert_source.py`
2. Read `wiki/sources/...`
3. Write or update one summary page
4. Merge into existing concept pages when possible
5. Update hash registry
6. Rebuild index
7. Append log entry

## Example 3: Sync Raw Directory

User request:

`把 raw 里新增的文档都同步进知识库`

Expected flow:

1. Run `scripts/sync_raw.py`
2. For each pending item:
   - convert source
   - write summary
   - update concepts
   - update registry
3. Rebuild index
4. Append log entries

## Example 4: Query And Save

User request:

`帮我总结一下这个知识库里关于估值的方法，并保存结果`

Expected flow:

1. Search `index.md`
2. Read relevant concepts
3. Read relevant summaries
4. Fall back to sources only if needed
5. Answer in the current thread
6. Save an exploration page
7. Rebuild index and append log
