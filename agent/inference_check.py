"""Milestone 13: check the local LLM before building the agent on it.

    python inference_check.py speed              # tokens/s, load time, GPU vs CPU split
    python inference_check.py rca                # baseline root-cause analysis, JSON output
    python inference_check.py rca --think        # same, with the model's thinking mode on

`rca` gives the model the real evidence of the demo-app v2 incident
(fixtures/v2-incident-evidence.json) with no runbooks and no agent: a baseline
to compare with once RAG (M12) and LangGraph (M15) are added. The answer must
match a Pydantic schema; Ollama constrains the output to it (`format`).

Talks to Ollama's HTTP API directly (stdlib only). LangChain arrives in M14.
Settings: OLLAMA_URL (from WSL: http://<windows host>:11434), LLM_MODEL (default gemma4:12b).
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from pydantic import ValidationError

from schemas import RootCauseAnalysis

FIXTURE = Path(__file__).parent / "fixtures" / "v2-incident-evidence.json"

# The Milestone 13 baseline prompt, kept as-is so `rca` stays comparable with
# the first measurements. The agent's improved prompt is in prompts/rca.py.
SYSTEM_PROMPT = """You are an experienced site reliability engineer investigating a production incident.
Use only the evidence provided. Never invent facts, values, files or commands that are not in it.
Keep facts (directly observed in the evidence) separate from inferences (your conclusions).
Be specific: name the exact configuration value, commit, file or line when the evidence shows it.
Set confidence by how directly the evidence supports the root cause, not by how plausible it sounds.
Any remediation you recommend will be executed only after human approval."""


def post(path: str, payload: dict, timeout: float = 600) -> dict:
    url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/") + path
    request = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def get(path: str) -> dict:
    url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/") + path
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def report_timing(reply: dict, wall: float) -> None:
    """Ollama reports durations in nanoseconds."""
    ns = 1e9
    load = reply.get("load_duration", 0) / ns
    prompt_tokens, prompt_s = reply.get("prompt_eval_count", 0), reply.get("prompt_eval_duration", 1) / ns
    out_tokens, out_s = reply.get("eval_count", 0), reply.get("eval_duration", 1) / ns
    print(f"  wall time        {wall:6.1f} s")
    print(f"  model load       {load:6.1f} s   (0 when the model was already in memory)")
    print(f"  prompt           {prompt_tokens:6d} tokens at {prompt_tokens / prompt_s:7.1f} tokens/s")
    print(f"  output           {out_tokens:6d} tokens at {out_tokens / out_s:7.1f} tokens/s")


def report_placement(model: str) -> None:
    """How much of the loaded model is in GPU memory (100% = no CPU offload)."""
    for m in get("/api/ps").get("models", []):
        if m["name"] == model:
            gpu = m["size_vram"] / m["size"] * 100 if m["size"] else 0
            print(f"  loaded size      {m['size'] / 2**30:6.1f} GiB, {gpu:.0f}% on GPU, "
                  f"context {m.get('context_length')} tokens")


def chat(model: str, messages: list[dict], num_ctx: int, think: bool, fmt: dict | None = None) -> tuple[dict, float]:
    payload = {
        "model": model, "messages": messages, "stream": False, "think": think,
        # temperature 0: the same evidence should give the same answer.
        # num_ctx: set per request; the server default here would be 262144.
        "options": {"temperature": 0, "num_ctx": num_ctx},
    }
    if fmt is not None:
        payload["format"] = fmt
    start = time.perf_counter()
    reply = post("/api/chat", payload)
    return reply, time.perf_counter() - start


def speed(model: str, num_ctx: int) -> None:
    prompt = "In three sentences: what is a Kubernetes readiness probe, and why can pods be Ready while requests fail?"
    for attempt in ("cold (may include loading the model)", "warm"):
        reply, wall = chat(model, [{"role": "user", "content": prompt}], num_ctx, think=False)
        print(f"\n{attempt}:")
        report_timing(reply, wall)
    report_placement(model)
    print(f"\nanswer:\n{reply['message']['content'].strip()}")


def rca(model: str, num_ctx: int, think: bool) -> None:
    evidence = json.loads(FIXTURE.read_text())
    evidence.pop("_source")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Find the root cause of this incident.\n\nEvidence:\n"
                                    + json.dumps(evidence, indent=2)},
    ]
    reply, wall = chat(model, messages, num_ctx, think, fmt=RootCauseAnalysis.model_json_schema())
    print(f"model {model}, thinking {'on' if think else 'off'}, num_ctx {num_ctx}")
    report_timing(reply, wall)
    report_placement(model)

    if think and reply["message"].get("thinking"):
        print(f"\nthinking ({len(reply['message']['thinking'])} chars), first 600:\n"
              f"{reply['message']['thinking'][:600]}...")

    try:
        result = RootCauseAnalysis.model_validate_json(reply["message"]["content"])
    except ValidationError as exc:
        print(f"\nINVALID structured output:\n{exc}\n\nraw:\n{reply['message']['content']}")
        sys.exit(1)
    print("\nvalid RootCauseAnalysis:")
    print(result.model_dump_json(indent=2))

    # Ground truth (never shown to the model): commit cd1ffef changed the default
    # database port to 5433; the database listens on 5432.
    text = (result.root_cause + " " + " ".join(result.inferences)).lower()
    print("\nground-truth checks:")
    print(f"  names port 5433          {'yes' if '5433' in text else 'NO'}")
    print(f"  names commit cd1ffef     {'yes' if 'cd1ffef' in text else 'NO'}")
    print(f"  recommends rollback      {'yes' if result.rollback_recommended else 'NO'}")
    print(f"  confidence               {result.confidence:.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("check", choices=["speed", "rca"])
    parser.add_argument("--model", default=os.getenv("LLM_MODEL", "gemma4:12b"))
    parser.add_argument("--ctx", type=int, default=8192, help="context window in tokens (default 8192)")
    parser.add_argument("--think", action="store_true", help="let the model reason before answering")
    args = parser.parse_args()
    if args.check == "speed":
        speed(args.model, args.ctx)
    else:
        rca(args.model, args.ctx, args.think)


if __name__ == "__main__":
    main()
