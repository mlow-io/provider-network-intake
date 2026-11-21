from __future__ import annotations

import json
import sys

import pytest

from provider_network_intake import CatalogStore

pytest.importorskip("mcp")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_real_stdio_client_inspects_previews_and_only_submits_proposals(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    payload = tmp_path / "roster.csv"
    payload.write_text("NPI,Site,Directory Flag\n1000000004,NORTH,Y\n")
    session_path = tmp_path / "session.json"
    session_path.write_text(
        json.dumps(
            {
                "artifacts": [
                    {
                        "id": "delivery-b",
                        "path": str(payload),
                        "context": {"source": "synthetic", "delivery_id": "b"},
                        "evidence": {
                            "note": "NPI is practitioner; Site is office key; Y requests exclusion."
                        },
                    }
                ]
            }
        )
    )
    catalog_path = tmp_path / "catalog.sqlite3"
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "provider_network_intake.mcp_server",
            "--session",
            str(session_path),
            "--catalog",
            str(catalog_path),
        ],
    )
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as client:
        await client.initialize()
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert names == {
            "list_contracts",
            "inspect_delivery",
            "match_variants",
            "get_variant",
            "get_findings",
            "preview_adaptation",
            "submit_adaptation_proposal",
        }
        assert not any("approve" in name or "accept" in name for name in names)
        inspected = await client.call_tool("inspect_delivery", {"artifact_id": "delivery-b"})
        assert not inspected.isError
        unknown = await client.call_tool(
            "inspect_delivery", {"artifact_id": "file:///unregistered"}
        )
        assert unknown.isError
        definition = {
            "version": 1,
            "source": "synthetic",
            "contract": "payer-network-roster/1",
            "grain": "practitioner_office",
            "headers": ["NPI", "Site", "Directory Flag"],
            "mapping": {
                "practitioner_npi": "NPI",
                "location_id": "Site",
                "directory_visible": "Directory Flag",
            },
            "transforms": {
                "directory_visible": [{"op": "value_map", "values": {"Y": False, "N": True}}]
            },
            "evidence_refs": ["note"],
        }
        preview = await client.call_tool(
            "preview_adaptation",
            {"artifact_id": "delivery-b", "definition": definition, "expected_catalog_revision": 0},
        )
        assert not preview.isError
        result = json.loads(preview.content[0].text)
        assert result["rows"][0]["normalized"]["directory_visible"] is False
        assert result["eligibility"]["state"] == "held"
        arguments = {
            "artifact_id": "delivery-b",
            "definition": definition,
            "expected_catalog_revision": 0,
            "request_id": "agent-proposal-b",
        }
        first = await client.call_tool("submit_adaptation_proposal", arguments)
        repeated = await client.call_tool("submit_adaptation_proposal", arguments)
        assert not first.isError and not repeated.isError
        assert json.loads(first.content[0].text)["id"] == json.loads(repeated.content[0].text)["id"]
        assert CatalogStore(catalog_path).snapshot().variants == ()
        assert CatalogStore(catalog_path).history() == []
