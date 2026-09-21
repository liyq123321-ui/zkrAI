#!/usr/bin/env python3
"""Run the Semantica audit-graph explorer for firstFlight.

This is the bridge between firstFlight's audit recorder (``backend/app/audit``)
and Semantica's two extensibility surfaces:

* **REST Explorer** -- a live FastAPI server that reads the *same* Neo4j graph
  the recorder writes. It exposes ``/api/info``, ``/health`` and the Explorer
  API (``/graph``, ``/decisions``, ``/provenance``, ``/sparql`` ...). This
  launcher starts it with ``uvicorn`` on the configured host/port.

* **MCP server** -- ``semantica-mcp`` is a *stdio* JSON-RPC server. MCP clients
  (IDEs, other agents) spawn it themselves; run ``--print-mcp-config`` to get
  the exact registration block for your client.

Note on ports: upstream's own ``semantica-server`` console script hard-codes
port 8000. This launcher instead imports the same ``semantica.server:app`` and
runs it through uvicorn, so it honours ``SEMATICA_SERVER_PORT`` (default 8000).

Usage:
    python tools/run_semantica_server.py                  # start REST Explorer
    python tools/run_semantica_server.py --check          # validate config only
    python tools/run_semantica_server.py --print-mcp-config
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Make the backend package importable regardless of CWD.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.config import Settings  # noqa: E402


def _apply_env(settings: Settings) -> None:
    """Project firstFlight Settings onto Semantica's environment contract."""
    if settings.semantica_backend == "neo4j":
        os.environ["GRAPH_STORE_DEFAULT_BACKEND"] = "neo4j"
        os.environ["GRAPH_STORE_NEO4J_URI"] = settings.semantica_neo4j_uri
        os.environ["GRAPH_STORE_NEO4J_USER"] = settings.semantica_neo4j_user
        os.environ["GRAPH_STORE_NEO4J_PASSWORD"] = settings.semantica_neo4j_password or ""
        os.environ["GRAPH_STORE_NEO4J_DATABASE"] = settings.semantica_neo4j_database

    # Explorer auth model:
    #   * SEMANTICA_API_KEY set              -> protected routes require X-API-Key
    #   * SEMANTICA_ALLOW_ANONYMOUS=true    -> open (localhost only; never expose)
    #   * neither                           -> protected routes return 503
    if settings.semantica_api_key:
        os.environ["SEMANTICA_API_KEY"] = settings.semantica_api_key
    os.environ["SEMANTICA_ALLOW_ANONYMOUS"] = (
        "true" if settings.semantica_allow_anonymous else "false"
    )
    os.environ["SEMANTICA_HOST"] = settings.semantica_server_host
    # Silence Semantica's per-node progress logging (see recorder.py for why).
    os.environ.setdefault("SEMANTICA_DISABLE_PROGRESS", "1")


def _mcp_config(settings: Settings) -> dict:
    venv_scripts = _BACKEND_ROOT / ".venv" / "Scripts"
    command = str(venv_scripts / ("semantica-mcp.exe" if os.name == "nt" else "semantica-mcp"))
    env = {
        "GRAPH_STORE_DEFAULT_BACKEND": "neo4j",
        "GRAPH_STORE_NEO4J_URI": settings.semantica_neo4j_uri,
        "GRAPH_STORE_NEO4J_USER": settings.semantica_neo4j_user,
        "GRAPH_STORE_NEO4J_PASSWORD": settings.semantica_neo4j_password or "",
        "GRAPH_STORE_NEO4J_DATABASE": settings.semantica_neo4j_database,
        "SEMANTICA_DISABLE_PROGRESS": "1",
    }
    if settings.semantica_api_key:
        env["SEMANTICA_API_KEY"] = settings.semantica_api_key
    return {
        "mcpServers": {
            "firstflight-semantica": {
                "command": command,
                "env": env,
            }
        }
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the Semantica audit-graph explorer for firstFlight"
    )
    parser.add_argument(
        "--check", action="store_true", help="Validate config and exit (0 = ok)"
    )
    parser.add_argument(
        "--print-mcp-config",
        action="store_true",
        help="Print the MCP client registration JSON and exit",
    )
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    _apply_env(settings)

    if args.print_mcp_config:
        print(json.dumps(_mcp_config(settings), indent=2, ensure_ascii=False))
        return 0

    # Gate: the audit layer must actually be enabled for the graph to exist.
    if not settings.semantica_enabled:
        print(
            "Semantica audit is DISABLED (SEMATICA_ENABLED != true). "
            "Enable it in backend/.env and start the backend first, then re-run this launcher.",
            file=sys.stderr,
        )
        return 1

    if args.check:
        print("Semantica config OK:")
        print(f"  backend : {settings.semantica_backend}")
        print(
            f"  neo4j   : {settings.semantica_neo4j_uri} "
            f"(db={settings.semantica_neo4j_database})"
        )
        print(
            f"  explorer: {settings.semantica_server_host}:{settings.semantica_server_port}"
        )
        return 0

    if not settings.semantica_api_key and not settings.semantica_allow_anonymous:
        print(
            "NOTE: neither SEMANTICA_API_KEY nor SEMANTICA_ALLOW_ANONYMOUS is set; "
            "the Explorer API routes will return 503. Set one of them in backend/.env "
            "(e.g. SEMANTICA_ALLOW_ANONYMOUS=true for local loopback dev).",
            file=sys.stderr,
        )

    import uvicorn
    from semantica.server import app

    print(
        f"Starting Semantica REST Explorer on "
        f"http://{settings.semantica_server_host}:{settings.semantica_server_port} "
        f"(reads Neo4j at {settings.semantica_neo4j_uri})"
    )
    uvicorn.run(
        app,
        host=settings.semantica_server_host,
        port=settings.semantica_server_port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
