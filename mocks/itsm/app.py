#!/usr/bin/env python3
"""Mock ITSM ticket system for AAP demos."""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from typing import Any

from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)
_lock = threading.Lock()
_counter = 1000
TICKETS: dict[str, dict[str, Any]] = {}

UI_TEMPLATE = """
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>ITSM Mock — Tickets DBA</title>
  <style>
    :root {
      --bg: #0f172a;
      --card: #1e293b;
      --text: #e2e8f0;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --ok: #22c55e;
      --warn: #f59e0b;
      --open: #60a5fa;
      --progress: #fbbf24;
      --closed: #34d399;
      --pending: #f87171;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; font-family: "Segoe UI", system-ui, sans-serif;
      background: linear-gradient(160deg, #0b1224, #172554 50%, #0f172a);
      color: var(--text); min-height: 100vh;
    }
    header {
      padding: 1.5rem 2rem; border-bottom: 1px solid #334155;
      display: flex; justify-content: space-between; align-items: center;
    }
    h1 { margin: 0; font-size: 1.4rem; letter-spacing: .02em; }
    .badge { color: var(--accent); font-size: .85rem; }
    main { padding: 1.5rem 2rem; max-width: 1100px; margin: 0 auto; }
    .empty { color: var(--muted); padding: 3rem 0; text-align: center; }
    .ticket {
      background: var(--card); border: 1px solid #334155; border-radius: 12px;
      padding: 1.25rem 1.5rem; margin-bottom: 1rem;
    }
    .row { display: flex; gap: 1rem; flex-wrap: wrap; align-items: center; }
    .id { font-size: 1.25rem; font-weight: 700; color: var(--accent); }
    .status {
      padding: .2rem .7rem; border-radius: 999px; font-size: .75rem;
      text-transform: uppercase; letter-spacing: .04em; font-weight: 700;
    }
    .status-open { background: rgba(96,165,250,.2); color: var(--open); }
    .status-diagnosed, .status-diagnostic_done { background: rgba(56,189,248,.2); color: var(--accent); }
    .status-in_progress { background: rgba(251,191,36,.2); color: var(--progress); }
    .status-validation { background: rgba(245,158,11,.2); color: var(--warn); }
    .status-resolved, .status-closed { background: rgba(52,211,153,.2); color: var(--closed); }
    .status-pending { background: rgba(248,113,113,.2); color: var(--pending); }
    .meta { color: var(--muted); font-size: .9rem; margin-top: .5rem; }
    .desc { margin: .8rem 0; line-height: 1.45; }
    .objects { margin: .5rem 0 0; padding-left: 1.2rem; color: #cbd5e1; }
    .comments {
      margin-top: 1rem; border-top: 1px solid #334155; padding-top: .8rem;
    }
    .comment {
      font-size: .85rem; color: #cbd5e1; margin-bottom: .45rem;
      padding-left: .6rem; border-left: 2px solid #475569;
    }
    .ts { color: var(--muted); font-size: .75rem; }
  </style>
  <meta http-equiv="refresh" content="10"/>
</head>
<body>
  <header>
    <h1>ITSM Mock — Manutenção Preventiva PostgreSQL</h1>
    <div class="badge">{{ tickets|length }} ticket(s)</div>
  </header>
  <main>
    {% if not tickets %}
      <div class="empty">Nenhum ticket ainda. Execute o workflow do AAP para abrir um.</div>
    {% endif %}
    {% for t in tickets %}
      <article class="ticket">
        <div class="row">
          <div class="id">{{ t.ticket_id }}</div>
          <div class="status status-{{ t.status }}">{{ t.status }}</div>
          <div class="meta">{{ t.priority }} · {{ t.environment }} · {{ t.database }}</div>
        </div>
        <div class="meta">Responsável: {{ t.assignee }} · Criado: {{ t.created_at }} · Atualizado: {{ t.updated_at }}</div>
        <h3 style="margin:.8rem 0 .3rem">{{ t.title }}</h3>
        <div class="desc">{{ t.description }}</div>
        {% if t.affected_objects %}
          <strong>Objetos afetados:</strong>
          <ul class="objects">
            {% for obj in t.affected_objects %}
              <li>{{ obj }}</li>
            {% endfor %}
          </ul>
        {% endif %}
        {% if t.resolution %}
          <div class="desc"><strong>Resolução:</strong> {{ t.resolution }}</div>
        {% endif %}
        {% if t.comments %}
          <div class="comments">
            {% for c in t.comments %}
              <div class="comment"><span class="ts">{{ c.at }}</span> — {{ c.text }}</div>
            {% endfor %}
          </div>
        {% endif %}
      </article>
    {% endfor %}
  </main>
</body>
</html>
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_id() -> str:
    global _counter
    with _lock:
        _counter += 1
        return f"DBA-{_counter}"


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok", "service": "mock-itsm", "tickets": len(TICKETS)})


@app.get("/")
def ui():
    tickets = sorted(TICKETS.values(), key=lambda t: t["created_at"], reverse=True)
    return render_template_string(UI_TEMPLATE, tickets=tickets)


@app.get("/api/tickets")
def list_tickets():
    tickets = sorted(TICKETS.values(), key=lambda t: t["created_at"], reverse=True)
    return jsonify({"count": len(tickets), "results": tickets})


@app.post("/api/tickets")
def create_ticket():
    data = request.get_json(silent=True) or {}
    ticket_id = _next_id()
    ticket = {
        "ticket_id": ticket_id,
        "title": data.get("title", "Manutenção preventiva PostgreSQL"),
        "database": data.get("database", "unknown"),
        "environment": data.get("environment", "unknown"),
        "priority": data.get("priority", "medium"),
        "status": data.get("status", "open"),
        "description": data.get("description", ""),
        "affected_objects": data.get("affected_objects", []),
        "assignee": data.get("assignee", "Ansible Automation Platform"),
        "resolution": data.get("resolution"),
        "comments": [],
        "created_at": _now(),
        "updated_at": _now(),
        "metadata": data.get("metadata", {}),
    }
    if data.get("comment"):
        ticket["comments"].append({"at": _now(), "text": data["comment"]})

    with _lock:
        TICKETS[ticket_id] = ticket

    return jsonify({"ticket_id": ticket_id, "status": ticket["status"]}), 201


@app.get("/api/tickets/<ticket_id>")
def get_ticket(ticket_id: str):
    ticket = TICKETS.get(ticket_id)
    if not ticket:
        return jsonify({"error": f"ticket '{ticket_id}' not found"}), 404
    return jsonify(ticket)


@app.patch("/api/tickets/<ticket_id>")
@app.put("/api/tickets/<ticket_id>")
def update_ticket(ticket_id: str):
    ticket = TICKETS.get(ticket_id)
    if not ticket:
        return jsonify({"error": f"ticket '{ticket_id}' not found"}), 404

    data = request.get_json(silent=True) or {}
    with _lock:
        for key in (
            "title",
            "database",
            "environment",
            "priority",
            "status",
            "description",
            "affected_objects",
            "assignee",
            "resolution",
            "metadata",
        ):
            if key in data:
                ticket[key] = data[key]
        if data.get("comment"):
            ticket["comments"].append({"at": _now(), "text": data["comment"]})
        ticket["updated_at"] = _now()

    return jsonify(ticket)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
