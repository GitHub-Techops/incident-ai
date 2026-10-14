"""Ask a question about an incident; the model decides which evidence to fetch.

    python ask.py INC-20261014-1a2b3c "What changed right before the incident?"
    python ask.py INC-20261014-1a2b3c "Are the pods healthy?"

Tool calling, step by step:
  1. The model gets the question and the tool descriptions.
  2. It replies either with an answer, or with tool calls ("call get_deployment_evidence").
  3. We run those tools (our code, our safety checks) and send the results back.
  4. Repeat until it answers, at most MAX_STEPS rounds.
This loop is how the chat UI will answer questions later; the incident
investigation itself runs as a fixed LangGraph workflow (Milestone 15).
"""

import sys

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from llm import make_chat_model
from prompts.rca import ASK_SYSTEM_PROMPT
from settings import Settings
from tools.backend import BackendClient, BackendError
from tools.incident_tools import make_incident_tools
from tools.knowledge import make_retriever

MAX_STEPS = 5


def ask(settings: Settings, incident_id: str, question: str) -> str:
    client = BackendClient(settings.backend_url)
    incident = client.get_incident(incident_id)
    tools = make_incident_tools(client, incident_id, make_retriever(settings, service=incident.get("service")))
    by_name = {t.name: t for t in tools}
    llm = make_chat_model(settings).bind_tools(tools)

    messages = [
        SystemMessage(ASK_SYSTEM_PROMPT.format(incident_id=incident_id, alert_summary=incident.get("summary"))),
        HumanMessage(question),
    ]
    for step in range(1, MAX_STEPS + 1):
        reply = llm.invoke(messages)
        messages.append(reply)
        tokens = (reply.usage_metadata or {}).get("input_tokens")
        if not reply.tool_calls:
            print(f"[step {step}] answer ({tokens} prompt tokens)")
            return reply.content
        for call in reply.tool_calls:
            print(f"[step {step}] model calls {call['name']}({call['args'] or ''})")
            tool = by_name.get(call["name"])
            result = tool.invoke(call["args"]) if tool else f"Unknown tool {call['name']}"
            messages.append(ToolMessage(result, tool_call_id=call["id"]))
    return f"(no answer after {MAX_STEPS} steps)"


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    try:
        print("\n" + ask(Settings.from_env(), sys.argv[1], sys.argv[2]))
    except BackendError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
