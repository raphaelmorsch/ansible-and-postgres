#!/usr/bin/env python3
"""Mock corporate secrets vault for AAP demos."""

from __future__ import annotations

import os
from flask import Flask, jsonify, request

app = Flask(__name__)

SECRETS = {
    "postgresql-prod": {
        "username": os.environ.get("PG_USERNAME", "user1"),
        "password": os.environ.get("PG_PASSWORD", "password1"),
        "database": os.environ.get("PG_DATABASE", "postgres"),
        "host": os.environ.get("PG_HOST", "postgresql.postgresql.svc.cluster.local"),
        "port": int(os.environ.get("PG_PORT", "5432")),
    },
    "postgresql-dev": {
        "username": os.environ.get("PG_USERNAME", "user1"),
        "password": os.environ.get("PG_PASSWORD", "password1"),
        "database": os.environ.get("PG_DATABASE", "postgres"),
        "host": os.environ.get("PG_HOST", "postgresql.postgresql.svc.cluster.local"),
        "port": int(os.environ.get("PG_PORT", "5432")),
    },
}


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok", "service": "mock-vault"})


@app.get("/secrets/<path:secret_name>")
def get_secret(secret_name: str):
    secret = SECRETS.get(secret_name)
    if not secret:
        return jsonify({"error": f"secret '{secret_name}' not found"}), 404

    # Mask password in UI-friendly endpoint when ?mask=1
    if request.args.get("mask") == "1":
        masked = dict(secret)
        masked["password"] = "********"
        return jsonify(masked)

    return jsonify(secret)


@app.get("/")
def index():
    return jsonify(
        {
            "service": "Mock Vault",
            "description": "Corporate secrets mock for AAP PostgreSQL maintenance demo",
            "endpoints": {
                "health": "/healthz",
                "list_compatible": "/secrets/<name>",
                "examples": ["/secrets/postgresql-prod", "/secrets/postgresql-dev"],
            },
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
