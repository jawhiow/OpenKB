"""Q&A agent for querying the OpenKB knowledge base."""
from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any

from agents import Agent, Runner, function_tool

from agents import ToolOutputImage, ToolOutputText
from openkb.agent.tools import (
    get_market_snapshot,
    get_wiki_page_content,
    read_wiki_file,
    read_wiki_image,
    search_long_document_pages,
)
from openkb.llm_usage import llm_usage_context
from openkb.llm_runtime import build_agent_model_settings, resolve_agent_model

MAX_TURNS = 50
from openkb.schema import LEGACY_WIKI_GUIDANCE, get_agents_md

_QUERY_INSTRUCTIONS_TEMPLATE = """\
You are OpenKB, a knowledge-base Q&A agent. You answer questions by searching the wiki.

{schema_md}

## Search strategy
1. Read index.md to see all documents and investment pages with brief summaries.
   Each document is marked (short), (pageindex), or (local-long) to indicate its type.
2. Read relevant summary pages (summaries/) for document overviews.
   Summaries may omit details — if you need more, follow the summary's
   `full_text` frontmatter field to the source (see step 7).
3. Read company pages (companies/) for company-specific investment evidence,
   ratings, valuation context, catalysts, risks, and exposure chains.
4. Read industry pages (industries/) when the question asks about sector
   structure, value chains, capacity cycles, or competitive dynamics.
5. Read concept pages (concepts/) for general cross-document synthesis,
   including reusable themes, metrics, risks, mechanisms, monitored
   indicators, and bear-case evidence.
   {legacy_wiki_guidance}
6. If `evidence_map.json` exists, read it when answering questions that need
   exact source support. It maps wiki pages to source summaries, page numbers,
   and short evidence snippets.
7. When you need detailed source document content, each summary page has a
    `full_text` frontmatter field with the path to the original document content:
    - Short documents (doc_type: short): read_file with that path.
    - PageIndex documents (doc_type: pageindex): if the exact page range is not
      obvious, call search_long_documents(query, doc_name, top_k) first. Then use
      get_page_content(doc_name, pages) with tight page ranges.
    - Local long documents (doc_type: local-long): if the exact page range is not
      obvious, call search_long_documents(query, doc_name, top_k) first. Then use
      get_page_content(doc_name, pages) with tight page ranges.
    Never fetch the whole long document when a tight page range is enough.
8. Source content may reference images (e.g. ![image](sources/images/doc/file.png)).
   Use the get_image tool to view them when needed.
9. For investment questions involving current price, PE/PB, market cap, or
   ETF/fund NAV, call ``market_snapshot(entity_or_symbol)``. Inputs may be a
   company name, registry canonical_id, or xueqiu symbol (e.g. "腾讯控股",
   "SH601127"). When citing a number from the snapshot:
   - Always include ``source`` and ``as_of`` so the user can audit freshness.
   - If the snapshot returns ``stale: true`` or includes a ``disclaimer``
     field, prefix the answer with a freshness warning and suggest
     ``openkb market refresh`` rather than treating the figure as live.
   - If the tool returns ``error: "unresolved_entity_or_symbol"``, do not
     guess a ticker; tell the user the company is not in the registry.
   - If the tool returns ``error: "no_snapshot_cached"``, surface the hint
     verbatim instead of falling back to wiki content for prices.
10. Synthesize a clear, concise, well-cited answer grounded in wiki content.

Answer based only on wiki content. Be concise.
Before each tool call, output one short sentence explaining the reason.

If you cannot find relevant information, say so clearly.
"""


class QueryReferenceTracker:
    """Collect concrete wiki resources read during one query turn."""

    def __init__(self) -> None:
        self._seen: set[tuple[tuple[str, Any], ...]] = set()
        self._references: list[dict[str, Any]] = []

    def add(self, reference: dict[str, Any]) -> None:
        key = tuple(sorted(reference.items()))
        if key in self._seen:
            return
        self._seen.add(key)
        self._references.append(reference)

    def references(self) -> list[dict[str, Any]]:
        return list(self._references)


def _format_query_reference(reference: dict[str, Any]) -> str | None:
    ref_type = str(reference.get("type") or "").strip()
    if ref_type == "wiki_file":
        path = str(reference.get("path") or "").strip().replace("\\", "/")
        if not path:
            return None
        link_target = path[:-3] if path.endswith(".md") else path
        return f"- [[{link_target}]]"
    if ref_type == "source_pages":
        path = str(reference.get("path") or "").strip().replace("\\", "/")
        pages = str(reference.get("pages") or "").strip()
        if path and pages:
            return f"- {path} pages {pages}"
        if path:
            return f"- {path}"
        return None
    if ref_type == "long_document_search":
        query = str(reference.get("query") or "").strip()
        doc_name = str(reference.get("doc_name") or "").strip()
        top_k = reference.get("top_k")
        parts = []
        if query:
            parts.append(f'query="{query}"')
        if doc_name:
            parts.append(f'doc_name="{doc_name}"')
        if top_k not in (None, "", 5):
            parts.append(f"top_k={top_k}")
        return f"- search_long_documents({', '.join(parts)})" if parts else "- search_long_documents"
    if ref_type == "image":
        image_path = str(reference.get("path") or "").strip().replace("\\", "/")
        return f"- {image_path}" if image_path else None
    path = str(reference.get("path") or "").strip().replace("\\", "/")
    return f"- {path}" if path else None


def format_query_exploration(question: str, answer: str, references: list[dict[str, Any]]) -> str:
    """Format a saved query exploration with a read set."""
    lines = [
        "---",
        f'query: "{question}"',
        "generated_by: openkb query",
        "---",
        "",
        f"# Query: {question}",
        "",
        answer.rstrip(),
        "",
        "## Read Set",
    ]

    formatted_references = [
        line
        for line in (
            _format_query_reference(reference)
            for reference in references
        )
        if line
    ]
    if formatted_references:
        lines.extend(formatted_references)
    else:
        lines.append("No tracked references were captured for this answer.")

    lines.append("")
    return "\n".join(lines)


async def run_with_query_model_pool(
    kb_dir: Path,
    model: str,
    operation: Any,
) -> Any:
    from openkb.cli import _setup_llm_key
    from openkb.model_pool import (
        configured_routes,
        is_model_pool_enabled,
        record_route_failure,
        record_route_success,
        route_profile,
        select_model_route,
    )

    if not is_model_pool_enabled(kb_dir):
        return await operation(model, None)

    excluded_routes: set[str] = set()
    last_error: Exception | None = None
    max_attempts = max(len(configured_routes(kb_dir)), 1)
    for _attempt in range(max_attempts):
        route = select_model_route(kb_dir, exclude=excluded_routes)
        _setup_llm_key(kb_dir, route_profile(route))
        try:
            result = await operation(route.model, route)
            record_route_success(kb_dir, route.profile_id, route.model)
            return result
        except Exception as exc:
            last_error = exc
            excluded_routes.add(route.route_id)
            record_route_failure(kb_dir, route.profile_id, route.model, exc)
    raise last_error or RuntimeError("Model pool query failed.")


def build_query_agent(
    wiki_root: str,
    model: str,
    language: str = "en",
    *,
    reference_tracker: QueryReferenceTracker | None = None,
) -> Agent:
    """Build and return the Q&A agent."""
    schema_md = get_agents_md(Path(wiki_root))
    instructions = _QUERY_INSTRUCTIONS_TEMPLATE.format(
        schema_md=schema_md,
        legacy_wiki_guidance=LEGACY_WIKI_GUIDANCE,
    )
    instructions += f"\n\nIMPORTANT: Answer in {language} language."

    @function_tool
    def read_file(path: str) -> str:
        """Read a Markdown file from the wiki.
        Args:
            path: File path relative to wiki root (e.g. 'summaries/paper.md').
        """
        if reference_tracker is not None:
            reference_tracker.add({"type": "wiki_file", "path": path})
        return read_wiki_file(path, wiki_root)

    @function_tool
    def search_long_documents(query: str, doc_name: str = "", top_k: int = 5) -> str:
        """Find relevant pages in PageIndex/local-long documents.

        Use when a long-document summary does not make the exact page range
        obvious. This searches local PageIndex tree summaries and per-page JSON
        sources; it does not require live PageIndex credentials.

        Args:
            query: Search phrase or question to locate in long documents.
            doc_name: Optional document name without extension. Leave blank to
                search across all long documents.
            top_k: Maximum number of page hits to return.
        """
        if reference_tracker is not None:
            reference_tracker.add(
                {
                    "type": "long_document_search",
                    "query": query,
                    "doc_name": doc_name,
                    "top_k": top_k,
                }
            )
        return search_long_document_pages(query, wiki_root, doc_name=doc_name, top_k=top_k)

    @function_tool
    def get_page_content(doc_name: str, pages: str) -> str:
        """Get text content of specific pages from a long document.
        Use for documents with doc_type: pageindex or local-long. For short
        documents, use read_file instead.
        Args:
            doc_name: Document name (e.g. 'attention-is-all-you-need').
            pages: Page specification (e.g. '3-5,7,10-12').
        """
        if reference_tracker is not None:
            reference_tracker.add(
                {
                    "type": "source_pages",
                    "path": f"sources/{doc_name}.json",
                    "doc_name": doc_name,
                    "pages": pages,
                }
            )
        return get_wiki_page_content(doc_name, pages, wiki_root)

    @function_tool
    def get_image(image_path: str) -> ToolOutputImage | ToolOutputText:
        """View an image from the wiki.

        Use when a question asks about a specific figure, chart, or diagram
        you'd need to see to answer accurately.

        Args:
            image_path: Image path relative to wiki root (e.g. 'sources/images/doc/p1_img1.png').
        """
        if reference_tracker is not None:
            reference_tracker.add({"type": "image", "path": image_path})
        result = read_wiki_image(image_path, wiki_root)
        if result["type"] == "image":
            return ToolOutputImage(image_url=result["image_url"])
        return ToolOutputText(text=result["text"])

    kb_root = str(Path(wiki_root).resolve().parent)

    @function_tool
    def market_snapshot(entity_or_symbol: str) -> str:
        """Return cached market snapshot for a company or xueqiu symbol.

        Use this for current-price / PE / PB / market-cap questions about
        equities resolved in the entity registry. When ``stale`` is true the
        answer MUST prefix a freshness disclaimer.

        Args:
            entity_or_symbol: Company alias, canonical_id, or xueqiu symbol
                (e.g. '腾讯控股', 'SH601127').
        """
        if reference_tracker is not None:
            reference_tracker.add({"type": "market_snapshot", "input": entity_or_symbol})
        result = get_market_snapshot(entity_or_symbol, kb_root)
        return _json.dumps(result, ensure_ascii=False)

    return Agent(
        name="wiki-query",
        instructions=instructions,
        tools=[read_file, search_long_documents, get_page_content, get_image, market_snapshot],
        model=resolve_agent_model(model),
        model_settings=build_agent_model_settings(parallel_tool_calls=False, model=model),
    )


async def run_query(
    question: str,
    kb_dir: Path,
    model: str,
    stream: bool = False,
    *,
    raw: bool = False,
    reference_tracker: QueryReferenceTracker | None = None,
) -> str:
    """Run a Q&A query against the knowledge base.

    Args:
        question: The user's question.
        kb_dir: Root of the knowledge base.
        model: LLM model name.
        stream: If True, print response tokens to stdout as they arrive.
        raw: If True, write raw markdown source instead of rendering it
            (still keeps tool-call line styling).

    Returns:
        The agent's final answer as a string.
    """
    import sys
    from agents import RawResponsesStreamEvent, RunItemStreamEvent, ItemHelpers
    from openai.types.responses import ResponseTextDeltaEvent
    from openkb.config import load_config

    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    language: str = config.get("language", "en")

    wiki_root = str(kb_dir / "wiki")

    if not stream:
        async def _run_once(effective_model: str, _route: Any) -> str:
            agent = build_query_agent(
                wiki_root,
                effective_model,
                language=language,
                reference_tracker=reference_tracker,
            )
            with llm_usage_context(kb_dir, "query"):
                result = await Runner.run(agent, question, max_turns=MAX_TURNS)
            return result.final_output or ""

        return await run_with_query_model_pool(kb_dir, model, _run_once)

    import os
    use_color = sys.stdout.isatty() and not os.environ.get("NO_COLOR", "")

    from openkb.agent.chat import (
        _build_style,
        _fmt,
        _format_tool_line,
        _make_markdown,
        _make_rich_console,
    )

    style = _build_style(use_color)

    from rich.live import Live

    if use_color and not raw:
        console = _make_rich_console()
    else:
        console = None  # type: ignore[assignment]

    def _start_live() -> Live | None:
        if console is None:
            return None
        lv = Live(console=console, vertical_overflow="visible")
        lv.start()
        return lv

    async def _run_stream_once(effective_model: str, _route: Any) -> str:
        agent = build_query_agent(
            wiki_root,
            effective_model,
            language=language,
            reference_tracker=reference_tracker,
        )
        live: Live | None = None
        last_was_text = False
        need_blank_before_text = False
        collected: list[str] = []
        segment: list[str] = []
        try:
            live = _start_live()
            with llm_usage_context(kb_dir, "query"):
                result = Runner.run_streamed(agent, question, max_turns=MAX_TURNS)
                async for event in result.stream_events():
                    if isinstance(event, RawResponsesStreamEvent):
                        if isinstance(event.data, ResponseTextDeltaEvent):
                            text = event.data.delta
                            if text:
                                if need_blank_before_text:
                                    if console is not None:
                                        print()
                                        segment = []
                                        live = _start_live()
                                    else:
                                        sys.stdout.write("\n")
                                    need_blank_before_text = False
                                collected.append(text)
                                segment.append(text)
                                last_was_text = True
                                if live:
                                    if "\n" in text:
                                        joined = "".join(segment)
                                        visible = joined[: joined.rfind("\n") + 1]
                                        if visible:
                                            live.update(_make_markdown(visible))
                                else:
                                    sys.stdout.write(text)
                                    sys.stdout.flush()
                    elif isinstance(event, RunItemStreamEvent):
                        item = event.item
                        if item.type == "tool_call_item":
                            if last_was_text:
                                if live:
                                    if segment:
                                        live.update(_make_markdown("".join(segment)))
                                    live.stop()
                                    live = None
                                else:
                                    sys.stdout.write("\n")
                                    sys.stdout.flush()
                                last_was_text = False
                            raw_item = item.raw_item
                            name = getattr(raw_item, "name", "?")
                            args = getattr(raw_item, "arguments", "") or ""
                            if live:
                                live.stop()
                                live = None
                            _fmt(style, ("class:tool", _format_tool_line(name, args) + "\n"))
                            need_blank_before_text = True
                        elif item.type == "tool_call_output_item":
                            pass
        finally:
            if live:
                if segment:
                    live.update(_make_markdown("".join(segment)))
                live.stop()
            print()
        return "".join(collected) if collected else result.final_output or ""

    return await run_with_query_model_pool(kb_dir, model, _run_stream_once)


async def run_query_session(
    question: str,
    kb_dir: Path,
    model: str,
    session: object,
    *,
    route: object | None = None,
) -> dict[str, Any]:
    """Run one non-streaming Q&A turn and persist it to a chat session."""
    from agents import Runner
    from openkb.config import load_config

    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    language: str = getattr(session, "language", "") or config.get("language", "en")
    wiki_root = str(kb_dir / "wiki")
    reference_tracker = QueryReferenceTracker()
    new_input = getattr(session, "history", []) + [
        {"role": "user", "content": question}
    ]
    async def _run_once(effective_model: str, _selected_route: Any) -> dict[str, Any]:
        agent = build_query_agent(
            wiki_root,
            effective_model,
            language=language,
            reference_tracker=reference_tracker,
        )
        with llm_usage_context(kb_dir, "query"):
            result = await Runner.run(agent, new_input, max_turns=MAX_TURNS)
        answer = result.final_output or ""
        session.record_turn(question, answer, result.to_input_list())
        return {
            "answer": answer,
            "session": session,
            "references": reference_tracker.references(),
        }

    if route is not None:
        return await _run_once(str(getattr(route, "model", "") or model), route)
    return await run_with_query_model_pool(kb_dir, model, _run_once)


async def run_query_session_stream(
    question: str,
    kb_dir: Path,
    model: str,
    session: object,
    on_delta: Any,
    on_status: Any | None = None,
    route: object | None = None,
) -> dict[str, Any]:
    """Run one streaming Q&A turn, persist it, and return answer metadata."""
    from agents import RawResponsesStreamEvent, RunItemStreamEvent, Runner
    from openai.types.responses import ResponseTextDeltaEvent
    from openkb.config import load_config

    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    language: str = getattr(session, "language", "") or config.get("language", "en")
    wiki_root = str(kb_dir / "wiki")
    reference_tracker = QueryReferenceTracker()
    new_input = getattr(session, "history", []) + [
        {"role": "user", "content": question}
    ]

    async def _emit_status(message: str) -> None:
        if on_status is not None:
            await on_status(message)

    def _tool_status(tool_name: str) -> str:
        return {
            "read_file": "Reading wiki context...",
            "search_long_documents": "Searching long documents...",
            "get_page_content": "Reading source pages...",
            "get_image": "Inspecting source image...",
        }.get(tool_name, f"Running {tool_name}...")

    async def _run_once(effective_model: str, _selected_route: Any) -> dict[str, Any]:
        agent = build_query_agent(
            wiki_root,
            effective_model,
            language=language,
            reference_tracker=reference_tracker,
        )
        collected: list[str] = []
        with llm_usage_context(kb_dir, "query"):
            await _emit_status("Running query...")
            result = Runner.run_streamed(agent, new_input, max_turns=MAX_TURNS)
            async for event in result.stream_events():
                if isinstance(event, RawResponsesStreamEvent) and isinstance(
                    event.data,
                    ResponseTextDeltaEvent,
                ):
                    text = event.data.delta
                    if text:
                        collected.append(text)
                        await on_delta(text)
                elif isinstance(event, RunItemStreamEvent):
                    item = event.item
                    if item.type == "tool_call_item":
                        raw_item = item.raw_item
                        name = str(getattr(raw_item, "name", "") or "tool").strip()
                        await _emit_status(_tool_status(name))
        answer = "".join(collected) if collected else result.final_output or ""
        session.record_turn(question, answer, result.to_input_list())
        return {
            "answer": answer,
            "session": session,
            "references": reference_tracker.references(),
        }

    if route is not None:
        return await _run_once(str(getattr(route, "model", "") or model), route)
    return await run_with_query_model_pool(kb_dir, model, _run_once)
