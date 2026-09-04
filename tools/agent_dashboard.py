"""Real-time subagent progress dashboard for the current ZCode session.

Serves a single dark-themed page on http://127.0.0.1:8766 that polls /api/agents
every 2 s. Progress is derived from ZCode's own rollout logs: for each spawned
subagent we read ~/.zcode/cli/agents/<sess>/agent_<id>/metadata.json (identity)
and ~/.zcode/cli/rollout/model-io-sess_subagent_<agentId>.jsonl (per-turn model
I/O records with timestamps), extracting tool calls, touched files and the
last activity so the page shows a live activity feed per agent.

Stdlib only; read-only. Run: python tools/agent_dashboard.py [port]
"""
from __future__ import annotations

import glob
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AGENTS_ROOT = os.path.expanduser(r"~\.zcode\cli\agents")
ROLLOUT_DIR = os.path.expanduser(r"~\.zcode\cli\rollout")
SESSION_ID = "sess_b7713431-922d-4849-aaeb-d764bb1936e7"  # this working session

KEY_INPUT_FIELDS = ("file_path", "path", "command", "description", "pattern", "prompt")


def parse_rollout(path: str, max_records: int = 400) -> list[dict]:
    """Parse the tail of a model-io rollout file into per-turn activity events."""
    events: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return events
    for line in lines[-max_records:]:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        ts = rec.get("completedAt") or rec.get("startedAt") or ""
        turn_tools: list[dict] = []
        texts: list[str] = []
        try:
            content = rec["response"]["body"]["content"]
        except (KeyError, TypeError):
            content = []
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_use":
                inp = item.get("input") or {}
                detail = ""
                for field in KEY_INPUT_FIELDS:
                    val = inp.get(field)
                    if val:
                        detail = str(val)
                        break
                if detail.startswith("[REDACTED"):
                    detail = ""
                turn_tools.append({"tool": item.get("name", "?"), "detail": detail[:160]})
            elif item.get("type") == "text" and item.get("text"):
                texts.append(str(item["text"])[:200])
        if turn_tools or texts:
            events.append({"ts": ts, "tools": turn_tools, "text": texts[-1] if texts else ""})
    return events


def agent_status(meta_path: str) -> str:
    if os.path.exists(meta_path.replace("metadata.json", "output.txt")):
        return "completed"
    return "running"


def collect() -> dict:
    agents = []
    now = datetime.now(timezone.utc)
    for meta_path in glob.glob(os.path.join(AGENTS_ROOT, SESSION_ID, "agent_*", "metadata.json")):
        try:
            meta = json.load(open(meta_path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        agent_id = meta.get("agentId", "")
        rollout = os.path.join(ROLLOUT_DIR, f"model-io-{meta.get('childSessionId', '')}.jsonl")
        events = parse_rollout(rollout)
        tool_count = sum(len(e["tools"]) for e in events)
        last_ts = events[-1]["ts"] if events else meta.get("createdAt", "")
        age_s = None
        if last_ts := (events[-1]["ts"] if events else None):
            try:
                t = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                age_s = (now - t).total_seconds()
            except ValueError:
                pass
        files: list[str] = []
        for e in reversed(events):
            for t in e["tools"]:
                if t["tool"] in ("Edit", "Write", "Read") and t["detail"]:
                    rel = re.sub(r"^D:\\MyTime\\?", "", t["detail"])
                    if rel not in files:
                        files.append(rel)
            if len(files) >= 6:
                break
        agents.append({
            "id": agent_id,
            "desc": meta.get("description", ""),
            "createdAt": meta.get("createdAt", ""),
            "status": agent_status(meta_path),
            "turns": len(events),
            "toolCalls": tool_count,
            "lastActivity": last_ts,
            "lastAgeSec": round(age_s) if age_s is not None else None,
            "files": files[:6],
            "recent": [
                {"ts": e["ts"],
                 "label": f'{t["tool"]}: {t["detail"]}' if t["detail"] else t["tool"]}
                for e in events[-6:] for t in e["tools"][-1:]
            ][-6:],
            "lastText": events[-1]["text"] if events else "",
        })
    agents.sort(key=lambda a: a["createdAt"], reverse=True)
    return {"now": datetime.now(timezone.utc).isoformat(), "agents": agents}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/api/agents":
            body = json.dumps(collect(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(PAGE.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args) -> None:  # keep the console clean
        pass


PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>子 Agent 进展 · v3.3.0</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root { --bg:#0f1216; --card:#171c23; --line:#262d38; --fg:#e8edf4; --dim:#8b97a6; --run:#4cc38a; --done:#5aa9ff; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.6 "Microsoft YaHei",sans-serif; }
  header { padding:22px 28px 10px; }
  h1 { font-size:22px; margin:0 0 4px; }
  #meta { color:var(--dim); font-size:13px; }
  main { max-width:1080px; margin:0 auto; padding:12px 20px 40px; }
  .agent { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px 20px; margin:14px 0; }
  .head { display:flex; align-items:center; gap:10px; }
  .dot { width:10px; height:10px; border-radius:50%; background:#555; flex:none; }
  .dot.run { background:var(--run); box-shadow:0 0 8px var(--run); animation:pulse 1.6s infinite; }
  .dot.done { background:var(--done); }
  @keyframes pulse { 50% { opacity:.4; } }
  .name { font-size:17px; font-weight:600; }
  .age { color:var(--dim); font-size:12.5px; margin-left:auto; }
  .stats { display:flex; gap:18px; color:var(--dim); font-size:13px; margin:8px 0 4px; }
  .stats b { color:var(--fg); }
  .files { margin:6px 0; }
  .file { display:inline-block; background:#20272f; border:1px solid var(--line); border-radius:6px;
          padding:2px 8px; margin:3px 4px 0 0; font-size:12px; color:#aebacd; font-family:Consolas,monospace; }
  .feed { margin-top:10px; border-top:1px solid var(--line); padding-top:8px; }
  .ev { display:flex; gap:10px; font-size:12.8px; color:var(--dim); padding:2.5px 0; white-space:nowrap; overflow:hidden; }
  .ev time { flex:none; color:#5b6774; font-family:Consolas,monospace; }
  .ev span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .last-text { color:#c8d2de; font-size:13px; margin-top:8px; background:#12171d; border-radius:8px; padding:8px 12px; }
  .empty { color:var(--dim); padding:30px; text-align:center; }
</style></head><body>
<header><h1>🛰 子 Agent 进展</h1><div id="meta">连接中…</div></header>
<main id="main"><div class="empty">加载中…</div></main>
<script>
const fmtAge = s => s==null ? '' : s<60 ? `${s}s 前` : s<3600 ? `${Math.floor(s/60)}m ${s%60}s 前` : `${Math.floor(s/3600)}h ${Math.floor(s%3600/60)}m 前`;
const fmtTs = t => t ? new Date(t).toLocaleTimeString('zh-CN',{hour12:false}) : '';
async function tick() {
  try {
    const d = await (await fetch('/api/agents')).json();
    document.getElementById('meta').textContent = `刷新于 ${new Date(d.now).toLocaleTimeString('zh-CN',{hour12:false})} · 共 ${d.agents.length} 个子 Agent`;
    const main = document.getElementById('main');
    if (!d.agents.length) { main.innerHTML = '<div class="empty">没有找到子 Agent 记录</div>'; return; }
    main.innerHTML = d.agents.map(a => {
      const running = a.status === 'running';
      const st = running ? '<span class="dot run"></span><span style="color:var(--run);font-size:12.5px;font-weight:600">运行中</span>'
                         : '<span class="dot done"></span><span style="color:var(--done);font-size:12.5px;font-weight:600">已完成</span>';
      return `<div class="agent">
        <div class="head">${st}<span class="name">${a.desc || a.id}</span><span class="age">${fmtAge(a.lastAgeSec)}</span></div>
        <div class="stats"><span>轮次 <b>${a.turns}</b></span><span>工具调用 <b>${a.toolCalls}</b></span></div>
        ${a.files.length ? `<div class="files">${a.files.map(f=>`<span class="file">${f}</span>`).join('')}</div>` : ''}
        ${a.lastText ? `<div class="last-text">${a.lastText}</div>` : ''}
        <div class="feed">${a.recent.map(e=>`<div class="ev"><time>${fmtTs(e.ts)}</time><span>${e.label}</span></div>`).join('')}</div>
      </div>`;
    }).join('');
  } catch (e) {
    document.getElementById('meta').textContent = '服务不可达，重试中…';
  }
}
setInterval(tick, 2000); tick();
</script></body></html>"""


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"agent dashboard: http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()