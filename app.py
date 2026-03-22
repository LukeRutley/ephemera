import os
from pathlib import Path
from typing import Any, TypedDict, cast
from uuid import uuid4

from flask import Flask, Response, jsonify, render_template, request, session
from dotenv import load_dotenv
from openai import OpenAI


BASE_DIR = Path(__file__).resolve().parent
CONTENT_FILE = BASE_DIR / "content.html"
HOLDING_PAGE_FILE = BASE_DIR / "holding_page.html"
load_dotenv(BASE_DIR / ".env")
SYSTEM_PROMPT = (BASE_DIR / "sys_prompt.md").read_text(encoding="utf-8").strip()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or "dev-secret-key"


class ThreadState(TypedDict):
    messages: list[str]
    html_history: list[str]
    current_html_index: int


THREADS: dict[str, ThreadState] = {}


def read_content_file() -> str:
    if CONTENT_FILE.exists():
        return CONTENT_FILE.read_text(encoding="utf-8")
    return ""


def read_holding_page_file() -> str:
    if HOLDING_PAGE_FILE.exists():
        return HOLDING_PAGE_FILE.read_text(encoding="utf-8")
    return ""


DEFAULT_HTML = read_content_file()


def write_content_file(html: str) -> None:
    CONTENT_FILE.write_text(html, encoding="utf-8")


def get_thread() -> ThreadState:
    session_id = session.get("session_id")
    if not session_id:
        session_id = uuid4().hex
        session["session_id"] = session_id

    return THREADS.setdefault(
        session_id,
        {"messages": [], "html_history": [], "current_html_index": -1},
    )


def get_client() -> OpenAI:
    return OpenAI()


def build_input(messages: list[str]) -> list[dict[str, object]]:
    return [
        {
            "role": "developer",
            "content": [{"type": "input_text", "text": SYSTEM_PROMPT}],
        },
        *[
            {
                "role": "user",
                "content": [{"type": "input_text", "text": message}],
            }
            for message in messages
        ],
    ]


def extract_html(response: object) -> str:
    output_text = getattr(response, "output_text", "")
    if output_text:
        return normalize_html(str(output_text))

    chunks: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", "")
            if text:
                chunks.append(str(text))
    return normalize_html("\n".join(chunks))


def normalize_html(html: str) -> str:
    cleaned = html.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def current_html(thread: ThreadState) -> str:
    html_history = thread["html_history"]
    current_index = int(thread["current_html_index"])
    if not html_history or current_index < 0:
        return read_content_file()
    return str(html_history[current_index])


def serialize_thread(thread: ThreadState) -> dict[str, object]:
    current_index = int(thread["current_html_index"])
    history_length = len(thread["html_history"])
    return {
        "html": current_html(thread),
        "messages": list(thread["messages"]),
        "can_go_back": current_index > 0,
        "can_go_forward": 0 <= current_index < history_length - 1,
    }


@app.get("/")
def index() -> str:
    return render_template(
        "index.html",
        state=serialize_thread(get_thread()),
        holding_page_html=read_holding_page_file(),
    )


@app.get("/content")
def content() -> Response:
    return Response(current_html(get_thread()), mimetype="text/html")


@app.post("/send")
def send() -> tuple[Response, int] | Response:
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()
    if not message:
        return jsonify({"error": "Message is required."}), 400

    thread = get_thread()
    messages = [*thread["messages"], message]

    try:
        response = get_client().responses.create(
            model="gpt-5.4-nano",
            input=cast(Any, build_input(messages)),
            text={"format": {"type": "text"}, "verbosity": "low"},
            reasoning=cast(Any, {"effort": "none", "summary": "auto"}),
            tools=[],
            store=True,
            include=cast(
                Any,
                [
                    "reasoning.encrypted_content",
                    "web_search_call.action.sources",
                ],
            ),
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    html = extract_html(response)
    if not html:
        return jsonify({"error": "OpenAI returned an empty response."}), 502

    current_index = int(thread["current_html_index"])
    if current_index < len(thread["html_history"]) - 1:
        thread["html_history"] = thread["html_history"][: current_index + 1]

    thread["messages"] = messages
    thread["html_history"].append(html)
    thread["current_html_index"] = len(thread["html_history"]) - 1
    write_content_file(html)
    return jsonify(serialize_thread(thread))


@app.post("/back")
def back() -> tuple[Response, int] | Response:
    thread = get_thread()
    current_index = int(thread["current_html_index"])
    if current_index <= 0:
        return jsonify({"error": "No previous HTML page available."}), 400

    thread["current_html_index"] = current_index - 1
    write_content_file(current_html(thread))
    return jsonify(serialize_thread(thread))


@app.post("/next")
def next_page() -> tuple[Response, int] | Response:
    thread = get_thread()
    current_index = int(thread["current_html_index"])
    if current_index < 0 or current_index >= len(thread["html_history"]) - 1:
        return jsonify({"error": "No next HTML page available."}), 400

    thread["current_html_index"] = current_index + 1
    write_content_file(current_html(thread))
    return jsonify(serialize_thread(thread))


@app.post("/new-topic")
def new_topic() -> Response:
    thread = get_thread()
    thread["messages"] = []
    thread["html_history"] = []
    thread["current_html_index"] = -1
    write_content_file(DEFAULT_HTML)
    return jsonify(serialize_thread(thread))


if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", "5000")))