from __future__ import annotations

import argparse
import json
import random
import string
from datetime import datetime, timezone
from pathlib import Path

from _common import dump_json, load_json


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gen_id() -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=3))
    return f"{ts}-{rand}"


def chats_dir(kb_dir: Path) -> Path:
    return kb_dir / ".openkb" / "chats"


class ChatSession:
    def __init__(
        self,
        *,
        kb_dir: Path,
        session_id: str,
        created_at: str,
        updated_at: str,
        title: str,
        turn_count: int,
        user_turns: list[str],
        assistant_texts: list[str],
    ) -> None:
        self.kb_dir = kb_dir
        self.id = session_id
        self.created_at = created_at
        self.updated_at = updated_at
        self.title = title
        self.turn_count = turn_count
        self.user_turns = user_turns
        self.assistant_texts = assistant_texts
        self.path = chats_dir(kb_dir) / f"{session_id}.json"

    @classmethod
    def new(cls, kb_dir: Path, title: str = "") -> "ChatSession":
        now = _utcnow_iso()
        return cls(
            kb_dir=kb_dir,
            session_id=_gen_id(),
            created_at=now,
            updated_at=now,
            title=title,
            turn_count=0,
            user_turns=[],
            assistant_texts=[],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "title": self.title,
            "turn_count": self.turn_count,
            "user_turns": self.user_turns,
            "assistant_texts": self.assistant_texts,
        }

    def save(self) -> None:
        dump_json(self.path, self.to_dict())

    def record_turn(self, user_message: str, assistant_text: str) -> None:
        self.user_turns.append(user_message)
        self.assistant_texts.append(assistant_text)
        self.turn_count = len(self.user_turns)
        if not self.title:
            self.title = user_message.strip()[:60]
        self.updated_at = _utcnow_iso()
        self.save()


def load_session(kb_dir: Path, session_id: str) -> ChatSession:
    data = load_json(chats_dir(kb_dir) / f"{session_id}.json", {})
    return ChatSession(
        kb_dir=kb_dir,
        session_id=data["id"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        title=data.get("title", ""),
        turn_count=data.get("turn_count", 0),
        user_turns=data.get("user_turns", []),
        assistant_texts=data.get("assistant_texts", []),
    )


def list_sessions(kb_dir: Path) -> list[dict]:
    out: list[dict] = []
    for path in chats_dir(kb_dir).glob("*.json"):
        data = load_json(path, {})
        if data:
            out.append(data)
    out.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return out


def delete_session(kb_dir: Path, session_id: str) -> bool:
    path = chats_dir(kb_dir) / f"{session_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect chat session storage for an agent-native KB.")
    parser.add_argument("kb_dir", nargs="?", default=".", help="Knowledge base root directory")
    args = parser.parse_args()
    print(json.dumps(list_sessions(Path(args.kb_dir).resolve()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
