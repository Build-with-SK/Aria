"""Functional compatibility of a model under Ollama /api/chat: native tool
calls, thinking separation, tool-result turns, parallel calls.
python -I compat.py MODEL [--num-gpu N] [--ctx 4096]"""
import argparse
import json
import time
import urllib.request

BASE = "http://127.0.0.1:11434"
TOOLS = [
    {"type": "function", "function": {
        "name": "get_quote", "description": "Latest price for a ticker symbol.",
        "parameters": {"type": "object", "properties": {
            "symbol": {"type": "string", "description": "ticker, e.g. AAPL"}},
            "required": ["symbol"]}}},
    {"type": "function", "function": {
        "name": "fx_rate", "description": "Exchange rate between two currencies.",
        "parameters": {"type": "object", "properties": {
            "base": {"type": "string"}, "quote": {"type": "string"}},
            "required": ["base", "quote"]}}},
]


def chat(model, messages, opts, think=None, tools=None, timeout=300):
    body = {"model": model, "messages": messages, "stream": False, "options": opts,
            "keep_alive": "10m"}
    if think is not None:
        body["think"] = think
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(BASE + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read())
    out["_wall"] = round(time.monotonic() - t, 2)
    return out


def show(label, out):
    m = out.get("message", {})
    print(json.dumps({"case": label, "wall_s": out["_wall"],
                      "eval_tokens": out.get("eval_count"),
                      "content": (m.get("content") or "")[:240],
                      "thinking_chars": len(m.get("thinking") or ""),
                      "thinking_head": (m.get("thinking") or "")[:120],
                      "tool_calls": m.get("tool_calls"),
                      "done_reason": out.get("done_reason")}))
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--num-gpu", type=int, default=None)
    ap.add_argument("--ctx", type=int, default=4096)
    a = ap.parse_args()
    opts = {"num_ctx": a.ctx, "temperature": 0, "num_predict": 400}
    if a.num_gpu is not None:
        opts["num_gpu"] = a.num_gpu
    model = a.model

    q = [{"role": "user", "content": "What is Apple's latest share price? Use the tool."}]
    m = show("tool_select_think_off", chat(model, q, opts, think=False, tools=TOOLS))
    calls = m.get("tool_calls") or []
    if calls:
        follow = q + [{"role": "assistant", "content": m.get("content") or "", "tool_calls": calls},
                      {"role": "tool", "tool_name": calls[0]["function"]["name"],
                       "content": json.dumps({"symbol": "AAPL", "price": 231.47, "currency": "USD"})}]
        show("tool_result_turn", chat(model, follow, opts, think=False, tools=TOOLS))

    p = [{"role": "user", "content": "Get the quotes for MSFT and NVDA, and the USD to GBP rate. Call all three tools at once."}]
    show("parallel_calls", chat(model, p, opts, think=False, tools=TOOLS))

    show("no_tool_needed", chat(model, [{"role": "user", "content": "Say hello in five words or fewer."}],
                                opts, think=False, tools=TOOLS))

    o2 = dict(opts, num_predict=1200)
    show("think_on", chat(model, [{"role": "user", "content": "Is 391 a prime number? Answer yes or no, then one short reason."}],
                          o2, think=True))
    show("think_off", chat(model, [{"role": "user", "content": "Is 391 a prime number? Answer yes or no, then one short reason."}],
                           opts, think=False))
    show("think_unspecified", chat(model, [{"role": "user", "content": "Reply with the single word: ok"}], o2))


main()
