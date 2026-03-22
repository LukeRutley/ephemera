import os
import json
import sqlite3
from pathlib import Path
from typing import Any, TypedDict, cast
from uuid import uuid4

from flask import Flask, Response, jsonify, render_template, request, session
from dotenv import load_dotenv
from openai import OpenAI


BASE_DIR = Path(__file__).resolve().parent
CONTENT_FILE = BASE_DIR / "content.html"
HOLDING_PAGE_FILE = BASE_DIR / "holding_page.html"
DATABASE_FILE = BASE_DIR / "ephemera.sqlite3"
load_dotenv(BASE_DIR / ".env")
SYSTEM_PROMPT = (BASE_DIR / "sys_prompt.md").read_text(encoding="utf-8").strip()
DATABASE_PROMPT = """
You have access to a local SQLite database.

Available runtime capabilities:
- Tool: inspect the database schema.
- Tool: execute single SQLite statements with full read/write access.
- Tool: execute multi-statement SQLite scripts for setup and migrations.
- Runtime HTTP API for generated pages:
    - GET /api/db/schema
    - POST /api/db/execute with JSON {"sql": "...", "params": [...]}
    - POST /api/db/execute with JSON {"mode": "script", "sql": "..."}

Use the database tools whenever the user asks for persistent data, structured storage, CRUD behavior, dashboards, lists, tables, or forms.
If the page depends on tables that do not exist yet, create them with the database tools before returning the final HTML.
When returning an interactive page, use absolute same-origin paths like /api/db/execute.
Prefer parameterized SQL for page-side writes and reads.
""".strip()
SCHEMA_CONSOLIDATION_PROMPT = """
You are performing SQLite schema maintenance for a local app.

Goal:
- Keep the data model compact, simple, and appropriate to the actual stored data.

Required workflow:
- Inspect the current schema first.
- Inspect the current data with targeted counts, sample rows, and table-level checks.
- Remove empty columns or tables that are not needed and compbine tables where possible.
- Identify redundant, overlapping, empty, or unnecessarily fragmented tables.
- If improvements are warranted, execute the necessary SQLite changes using the provided tools.
- Preserve existing data and keep migrations as small as possible.
- Avoid changing app_metadata unless it is necessary for consistency.
- Prefer creating replacement tables, copying data, then removing obsolete tables.
- Use the script tool for multi-step migrations.
- If a migration step fails, inspect the resulting state and continue from there instead of stopping.
- Handle partially completed migrations safely, including cleanup or reuse of temporary tables left behind by earlier failed attempts.
- Re-inspect the schema after any changes.

Return a concise plain-language summary covering:
1. What changed.
2. Why it was changed.
3. Which tables remain in the final schema.

If no changes were needed, say so explicitly.
""".strip()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or "dev-secret-key"


class ThreadState(TypedDict):
    messages: list[str]
    html_history: list[str]
    current_html_index: int


THREADS: dict[str, ThreadState] = {}

SQLITE_TOOLS: list[dict[str, object]] = [
    {
        "type": "function",
        "name": "inspect_sqlite_schema",
        "description": "Inspect the local SQLite database schema, including tables, views, and columns.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "run_sqlite_query",
        "description": "Execute a single SQL statement against the local SQLite database. Supports full read/write access.",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "A single SQLite statement to execute.",
                },
                "params": {
                    "type": "array",
                    "description": "Optional positional SQL parameters.",
                    "items": {},
                },
            },
            "required": ["sql"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "run_sqlite_script",
        "description": "Execute a multi-statement SQLite script for setup, migrations, or batched writes.",
        "parameters": {
            "type": "object",
            "properties": {
                "sql_script": {
                    "type": "string",
                    "description": "A SQLite script that may contain multiple statements separated by semicolons.",
                }
            },
            "required": ["sql_script"],
            "additionalProperties": False,
        },
    },
]


def read_content_file() -> str:
    if CONTENT_FILE.exists():
        return CONTENT_FILE.read_text(encoding="utf-8")
    return ""


def read_holding_page_file() -> str:
    if HOLDING_PAGE_FILE.exists():
        return HOLDING_PAGE_FILE.read_text(encoding="utf-8")
    return ""


def get_db_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_FILE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def ensure_database() -> None:
    with get_db_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            INSERT OR IGNORE INTO app_metadata (key, value)
            VALUES ('app_name', 'ephemera');
            """
        )
        connection.commit()


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def serialize_sql_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.hex()
    return value


def normalize_sql_params(params: object) -> list[object] | dict[str, object]:
    if params is None:
        return []
    if isinstance(params, list):
        return params
    if isinstance(params, dict):
        return params
    raise ValueError("SQL params must be an array, object, or null.")


def execute_sql(sql: str, params: object = None) -> dict[str, object]:
    statement = sql.strip()
    if not statement:
        raise ValueError("SQL is required.")

    normalized_params = normalize_sql_params(params)

    with get_db_connection() as connection:
        cursor = connection.execute(statement, normalized_params)
        columns = [column[0] for column in cursor.description] if cursor.description else []
        rows = [
            {column: serialize_sql_value(row[column]) for column in row.keys()}
            for row in cursor.fetchall()
        ] if columns else []
        affected_rows = cursor.rowcount if cursor.rowcount != -1 else 0
        last_insert_rowid = cursor.lastrowid
        connection.commit()

    return {
        "ok": True,
        "statement_type": statement.split(None, 1)[0].upper(),
        "columns": columns,
        "rows": rows,
        "row_count": len(rows) if columns else affected_rows,
        "affected_rows": affected_rows,
        "last_insert_rowid": last_insert_rowid,
    }


def execute_sql_script(sql_script: str) -> dict[str, object]:
    script = sql_script.strip()
    if not script:
        raise ValueError("SQL script is required.")

    with get_db_connection() as connection:
        connection.executescript(script)
        connection.commit()

    return {"ok": True, "message": "SQL script executed successfully."}


def get_database_schema() -> dict[str, object]:
    with get_db_connection() as connection:
        objects = connection.execute(
            """
            SELECT name, type, sql
            FROM sqlite_master
            WHERE type IN ('table', 'view')
              AND name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        ).fetchall()

        schema: list[dict[str, object]] = []
        for db_object in objects:
            object_name = str(db_object["name"])
            columns = connection.execute(
                f"PRAGMA table_info({quote_identifier(object_name)})"
            ).fetchall()
            schema.append(
                {
                    "name": object_name,
                    "type": str(db_object["type"]),
                    "sql": str(db_object["sql"] or ""),
                    "columns": [
                        {
                            "cid": int(column["cid"]),
                            "name": str(column["name"]),
                            "data_type": str(column["type"] or ""),
                            "not_null": bool(column["notnull"]),
                            "default_value": serialize_sql_value(column["dflt_value"]),
                            "is_primary_key": bool(column["pk"]),
                        }
                        for column in columns
                    ],
                }
            )

    return {"database_path": str(DATABASE_FILE), "objects": schema}


def run_database_tool(name: str, arguments: dict[str, object]) -> dict[str, object]:
    if name == "inspect_sqlite_schema":
        return get_database_schema()
    if name == "run_sqlite_query":
        return execute_sql(str(arguments.get("sql", "")), arguments.get("params"))
    if name == "run_sqlite_script":
        return execute_sql_script(str(arguments.get("sql_script", "")))
    raise ValueError(f"Unknown tool: {name}")


def execute_database_tool_safely(name: str, arguments: dict[str, object]) -> dict[str, object]:
    try:
        result = run_database_tool(name, arguments)
    except Exception as exc:
        return {
            "ok": False,
            "tool_name": name,
            "error_type": exc.__class__.__name__,
            "error": str(exc),
        }

    if "ok" not in result:
        return {"ok": True, "tool_name": name, "result": result}
    result["tool_name"] = name
    return result


DEFAULT_HTML = read_content_file()
ensure_database()


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
            "content": [
                {"type": "input_text", "text": SYSTEM_PROMPT},
                {"type": "input_text", "text": DATABASE_PROMPT},
            ],
        },
        *[
            {
                "role": "user",
                "content": [{"type": "input_text", "text": message}],
            }
            for message in messages
        ],
    ]


def build_tool_input(developer_texts: list[str], user_messages: list[str]) -> list[dict[str, object]]:
    return [
        {
            "role": "developer",
            "content": [{"type": "input_text", "text": text} for text in developer_texts],
        },
        *[
            {
                "role": "user",
                "content": [{"type": "input_text", "text": message}],
            }
            for message in user_messages
        ],
    ]


def extract_html(response: object) -> str:
    return normalize_html(extract_response_text(response))


def extract_response_text(response: object) -> str:
    output_text = getattr(response, "output_text", "")
    if output_text:
        return str(output_text).strip()

    chunks: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", "")
            if text:
                chunks.append(str(text))
    return "\n".join(chunks).strip()


def create_tool_response(
    developer_texts: list[str],
    user_messages: list[str],
    *,
    max_turns: int = 24,
) -> object:
    request_input: object = build_tool_input(developer_texts, user_messages)
    previous_response_id: str | None = None
    client = get_client()

    for _ in range(max_turns):
        request_payload = {
            "model": "gpt-5.4-nano",
            "input": cast(Any, request_input),
            "text": {"format": {"type": "text"}, "verbosity": "low"},
            "reasoning": cast(Any, {"effort": "none", "summary": "auto"}),
            "tools": cast(Any, SQLITE_TOOLS),
            "store": True,
            "include": cast(
                Any,
                [
                    "reasoning.encrypted_content",
                    "web_search_call.action.sources",
                ],
            ),
        }
        if previous_response_id:
            request_payload["previous_response_id"] = previous_response_id

        response = client.responses.create(**request_payload)
        function_calls = [
            item
            for item in (getattr(response, "output", []) or [])
            if getattr(item, "type", "") == "function_call"
        ]
        if not function_calls:
            return response

        tool_outputs: list[dict[str, str]] = []
        for call in function_calls:
            raw_arguments = getattr(call, "arguments", "{}") or "{}"
            call_name = str(getattr(call, "name", ""))
            try:
                parsed_arguments = json.loads(str(raw_arguments))
                if not isinstance(parsed_arguments, dict):
                    raise ValueError("Tool arguments must be a JSON object.")
                result = execute_database_tool_safely(call_name, parsed_arguments)
            except Exception as exc:
                result = {
                    "ok": False,
                    "tool_name": call_name,
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                }

            tool_outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": str(getattr(call, "call_id", "")),
                    "output": json.dumps(result),
                }
            )

        previous_response_id = str(getattr(response, "id", "") or "")
        request_input = tool_outputs

    raise RuntimeError(f"OpenAI tool-call limit exceeded after {max_turns} turns.")


def create_html_response(messages: list[str]) -> object:
    return create_tool_response([SYSTEM_PROMPT, DATABASE_PROMPT], messages)


def consolidate_database_schema() -> str:
    response = create_tool_response(
        [SCHEMA_CONSOLIDATION_PROMPT],
        [
            "Inspect the SQLite database, consolidate redundant or unnecessary tables if appropriate, and keep the schema compact and simple while preserving existing data.",
        ],
        max_turns=40,
    )
    return extract_response_text(response)


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
        response = create_html_response(messages)
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


@app.post("/consolidate-schema")
def consolidate_schema() -> tuple[Response, int] | Response:
    try:
        summary = consolidate_database_schema()
        schema = get_database_schema()
    except sqlite3.Error as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"ok": True, "summary": summary, "schema": schema})


@app.get("/api/db/schema")
def db_schema() -> tuple[Response, int] | Response:
    try:
        return jsonify(get_database_schema())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/db/execute")
def db_execute() -> tuple[Response, int] | Response:
    payload = request.get_json(silent=True) or {}
    sql = str(payload.get("sql", "")).strip()
    mode = str(payload.get("mode", "query")).strip().lower()

    if not sql:
        return jsonify({"error": "SQL is required."}), 400

    try:
        if mode == "script":
            result = execute_sql_script(sql)
        else:
            result = execute_sql(sql, payload.get("params"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except sqlite3.Error as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify(result)


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
    write_content_file("")
    return jsonify(serialize_thread(thread))


if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", "5000")))