"""Q&A agent for querying the OpenKB knowledge base."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agents import Agent, Runner, function_tool

from agents import ToolOutputImage, ToolOutputText
from openkb.agent.tools import get_wiki_page_content, read_wiki_file, read_wiki_image
from openkb.llm_runtime import build_agent_model_settings, resolve_agent_model

MAX_TURNS = 50
from openkb.schema import get_agents_md

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
4. Read industry/theme/metric/risk pages (industries/, themes/, metrics/,
   risks/) when the question asks about sector structure, investment themes,
   monitored indicators, or bear-case evidence.
5. Read concept pages (concepts/) for general cross-document synthesis.
6. If `evidence_map.json` exists, read it when answering questions that need
   exact source support. It maps wiki pages to source summaries, page numbers,
   and short evidence snippets.
7. When you need detailed source document content, each summary page has a
   `full_text` frontmatter field with the path to the original document content:
   - Short documents (doc_type: short): read_file with that path.
   - PageIndex documents (doc_type: pageindex): use get_page_content(doc_name, pages)
     with tight page ranges. The summary shows document tree structure with page
     ranges to help you target.
   - Local long documents (doc_type: local-long): use get_page_content(doc_name, pages)
     with tight page ranges. These are locally extracted per-page JSON files.
   Never fetch the whole long document when a tight page range is enough.
8. Source content may reference images (e.g. ![image](sources/images/doc/file.png)).
   Use the get_image tool to view them when needed.
9. Synthesize a clear, concise, well-cited answer grounded in wiki content.

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


def build_query_agent(
    wiki_root: str,
    model: str,
    language: str = "en",
    *,
    reference_tracker: QueryReferenceTracker | None = None,
) -> Agent:
    """Build and return the Q&A agent."""
    schema_md = get_agents_md(Path(wiki_root))
    instructions = _QUERY_INSTRUCTIONS_TEMPLATE.format(schema_md=schema_md)
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
    return Agent(
        name="wiki-query",
        instructions=instructions,
        tools=[read_file, get_page_content, get_image],
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

    agent = build_query_agent(wiki_root, model, language=language)

    if not stream:
        result = await Runner.run(agent, question, max_turns=MAX_TURNS)
        return result.final_output or ""

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

    live: Live | None = None
    last_was_text = False
    need_blank_before_text = False
    result = Runner.run_streamed(agent, question, max_turns=MAX_TURNS)
    collected: list[str] = []
    segment: list[str] = []
    try:
        live = _start_live()
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
    effective_model = str(getattr(route, "model", "") or model)
    agent = build_query_agent(
        wiki_root,
        effective_model,
        language=language,
        reference_tracker=reference_tracker,
    )
    new_input = getattr(session, "history", []) + [
        {"role": "user", "content": question}
    ]
    result = await Runner.run(agent, new_input, max_turns=MAX_TURNS)
    answer = result.final_output or ""
    session.record_turn(question, answer, result.to_input_list())
    return {
        "answer": answer,
        "session": session,
        "references": reference_tracker.references(),
    }


async def run_query_session_stream(
    question: str,
    kb_dir: Path,
    model: str,
    session: object,
    on_delta: Any,
) -> dict[str, Any]:
    """Run one streaming Q&A turn, persist it, and return answer metadata."""
    from agents import RawResponsesStreamEvent, Runner
    from openai.types.responses import ResponseTextDeltaEvent
    from openkb.config import load_config

    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    language: str = getattr(session, "language", "") or config.get("language", "en")
    wiki_root = str(kb_dir / "wiki")
    reference_tracker = QueryReferenceTracker()
    agent = build_query_agent(
        wiki_root,
        model,
        language=language,
        reference_tracker=reference_tracker,
    )
    new_input = getattr(session, "history", []) + [
        {"role": "user", "content": question}
    ]
    result = Runner.run_streamed(agent, new_input, max_turns=MAX_TURNS)
    collected: list[str] = []
    async for event in result.stream_events():
        if isinstance(event, RawResponsesStreamEvent) and isinstance(
            event.data,
            ResponseTextDeltaEvent,
        ):
            text = event.data.delta
            if text:
                collected.append(text)
                await on_delta(text)
    answer = "".join(collected) if collected else result.final_output or ""
    session.record_turn(question, answer, result.to_input_list())
    return {
        "answer": answer,
        "session": session,
        "references": reference_tracker.references(),
    }
