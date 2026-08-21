#!/usr/bin/env python3
"""Validate OCI Responses API text output and remote MCP tool calling.

The script reads OCI settings from local ``config.yaml`` and uses the configured
OCI CLI profile to sign requests. It first checks text output for every model in
the configured compartment, then checks MCP tool execution for response-capable
models, producing timestamped HTML and log artifacts under ``outputs/`` and
``logs/`` respectively.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html import escape
import json
from pathlib import Path
import sys
from typing import Any

import oci
from oci._vendor import requests
import yaml


CONFIG_PATH = Path(__file__).with_name("config.yaml")
OUTPUTS_DIR = Path(__file__).with_name("outputs")
LOGS_DIR = Path(__file__).with_name("logs")
DEFAULT_EXCLUDED_PROVIDERS = {"olm", "urchade"}
DEFAULT_MCP_SERVER_URL = "https://mcp.deepwiki.com/mcp"
DEFAULT_PROMPT = "In a few words, summarize the React framework structure using the DeepWiki MCP tool. Do NOT skip the MCP tool call."


class Tee:
    """Write console output to a run log without hiding it from the caller."""

    def __init__(self, *streams: Any) -> None:
        """Store the output streams that should receive each write."""
        self.streams = streams

    def write(self, data: str) -> int:
        """Write and flush text to every configured stream."""
        for stream in self.streams:
            stream.write(data)
            stream.flush()
        return len(data)

    def flush(self) -> None:
        """Flush every configured stream."""
        for stream in self.streams:
            stream.flush()


def load_settings() -> dict[str, str]:
    """Load OCI settings; OCI credentials remain in the standard CLI config."""
    try:
        settings = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        values = settings["oci"]
        return {
            "cli_profile": values["cli_profile"],
            "compartment_ocid": values["compartment_ocid"],
            "enterprise_ai_project_ocid": values["enterprise_ai_project_ocid"],
        }
    except (FileNotFoundError, KeyError, TypeError, yaml.YAMLError) as error:
        raise RuntimeError(f"Could not load OCI settings from {CONFIG_PATH}: {error}") from error


def normalize_profile(profile: str) -> str:
    """Convert the convenient lowercase default profile name to OCI's canonical form."""
    return "DEFAULT" if profile.lower() == "default" else profile


def list_models(config: dict[str, str], compartment_id: str) -> list[object]:
    """List and deterministically sort Generative AI models in a compartment."""
    client = oci.generative_ai.GenerativeAiClient(config)
    response = oci.pagination.list_call_get_all_results(client.list_models, compartment_id=compartment_id)
    return sorted(response.data, key=lambda item: (item.display_name or "", item.id))


def select_models(models: list[object], include: list[str], exclude: list[str]) -> list[object]:
    """Filter models by model name/ID/vendor; includes take precedence over defaults."""
    include_terms = [term.lower() for term in include]
    exclude_terms = [term.lower() for term in exclude]

    def searchable(model: object) -> str:
        """Combine a model's identifying fields into lowercase filter text."""
        return " ".join((str(model.display_name or ""), str(model.id or ""), str(model.vendor or ""))).lower()

    selected = []
    for model in models:
        haystack = searchable(model)
        provider = str(model.vendor or "").lower()
        if include_terms and not any(term in haystack for term in include_terms):
            continue
        if not include_terms and provider in DEFAULT_EXCLUDED_PROVIDERS:
            continue
        if any(term in haystack for term in exclude_terms):
            continue
        selected.append(model)
    return selected


def response_url(region: str) -> str:
    """Build the regional OCI OpenAI-compatible Responses API URL."""
    return f"https://inference.generativeai.{region}.oci.oraclecloud.com/openai/v1/responses"


def post_response(config: dict[str, str], project_id: str, payload: dict[str, Any], timeout: float) -> tuple[int | str, dict[str, Any]]:
    """Submit one OCI IAM-signed Responses API request."""
    signer = oci.signer.Signer(
        tenancy=config["tenancy"], user=config["user"], fingerprint=config["fingerprint"],
        private_key_file_location=config["key_file"], pass_phrase=config.get("pass_phrase"),
    )
    try:
        response = requests.post(response_url(config["region"]), json=payload, auth=signer, headers={"OpenAI-Project": project_id, "Content-Type": "application/json"}, timeout=timeout)
        return response.status_code, response.json() if response.content else {}
    except requests.RequestException as error:
        return "—", {"error": {"message": str(error)}}


def extract_output_text(data: dict[str, Any]) -> str:
    """Extract text from raw Responses API JSON (the SDK exposes this as output_text)."""
    texts = []
    for item in data.get("output", []) or []:
        for content in item.get("content", []) or []:
            if content.get("type") == "output_text" and content.get("text"):
                texts.append(str(content["text"]))
    return "\n".join(texts)


def validate_response(config: dict[str, str], project_id: str, model: object, args: argparse.Namespace) -> dict[str, Any]:
    """Verify that a model can produce a non-empty Responses API text output."""
    status_code, data = post_response(config, project_id, {"model": model.display_name, "input": "Reply with exactly: response validation passed.", "store": False}, args.timeout)
    output = extract_output_text(data)
    success = status_code == 200 and bool(output)
    error = data.get("error") or {}
    return {"status": "passed" if success else "failed", "http_status": status_code, "output": output, "error": "" if success else error.get("message", "Response did not contain text output.")}


def validate_mcp(config: dict[str, str], project_id: str, model: object, args: argparse.Namespace) -> dict[str, Any]:
    """Verify that a response includes text output and an executed MCP tool call."""
    payload = {"model": model.display_name, "input": args.prompt, "store": False, "tools": [{
            "type": "mcp", "server_label": "sample_mcp", "server_description": "Sample dice-rolling MCP server.",
            "server_url": args.mcp_server_url, "require_approval": "never",
        }]}
    status_code, data = post_response(config, project_id, payload, args.timeout)
    output = extract_output_text(data)
    tool_items = [item for item in data.get("output", []) if str(item.get("type", "")).startswith("mcp_")]
    mcp_called = any(item.get("type") == "mcp_call" for item in tool_items)
    success = status_code == 200 and bool(output) and mcp_called
    error = data.get("error") or {}
    return {"status": "passed" if success else "failed", "http_status": status_code, "output": output, "mcp_called": mcp_called, "error": "" if success else error.get("message", "Response did not contain both text output and an MCP tool call.")}


def render_html(results: list[dict[str, Any]], generated_at: datetime, args: argparse.Namespace) -> str:
    """Render the provider-grouped, filterable combined Response and MCP report."""
    response_results = [result for result in results if result["phase"] == "Response"]
    mcp_results = [result for result in results if result["phase"] == "MCP"]
    passed = sum(result["status"] == "passed" for result in results)
    mcp_by_id = {result["model_id"]: result for result in mcp_results}
    provider_groups: dict[str, list[dict[str, Any]]] = {}
    for response in response_results:
        provider_groups.setdefault(response["vendor"] or "Unknown", []).append(response)

    group_rows = []
    for provider in sorted(provider_groups, key=str.lower):
        provider_results = provider_groups[provider]
        response_passed = sum(item["status"] == "passed" for item in provider_results)
        provider_mcp = [mcp_by_id[item["model_id"]] for item in provider_results if item["model_id"] in mcp_by_id]
        mcp_passed = sum(item["status"] == "passed" for item in provider_mcp)
        mcp_summary = f"MCP: {mcp_passed}/{len(provider_mcp)} passed" if provider_mcp else "MCP: not run"
        provider_search = " ".join((provider, " ".join(item["name"] for item in provider_results))).lower()
        group_rows.append(
            f"<tr class=\"provider-row\" data-provider-group=\"{escape(provider)}\" data-search=\"{escape(provider_search)}\">"
            f"<td colspan=\"5\"><button class=\"provider-toggle\" type=\"button\" aria-expanded=\"false\">"
            f"<span class=\"provider-chevron\" aria-hidden=\"true\">▼</span><span>{escape(provider)}</span>"
            f"<span class=\"provider-summary\">Response: {response_passed}/{len(provider_results)} passed · {escape(mcp_summary)}</span>"
            f"<span class=\"provider-count\">{len(provider_results)} models</span></button></td></tr>"
        )
        for response in provider_results:
            mcp = mcp_by_id.get(response["model_id"])
            mcp_status = mcp["status"] if mcp else "not_run"
            mcp_detail = (mcp["output"] or mcp["error"] or "—") if mcp else "Not response-capable or excluded by MCP provider filters."
            search_text = " ".join((response["name"], provider, response["status"], mcp_status)).lower()
            group_rows.append(
                f"<tr class=\"model-row\" data-provider=\"{escape(provider)}\" data-search=\"{escape(search_text)}\" data-response=\"{escape(response['status'])}\" data-mcp=\"{escape(mcp_status)}\">"
                f"<td class=\"model-name\"><strong>{escape(response['name'])}</strong></td>"
                f"<td><span class=\"status {escape(response['status'])}\">{escape(response['status'].upper())}</span><br><small>HTTP {escape(str(response['http_status']))}</small></td>"
                f"<td class=\"output\">{escape(str(response['output'] or response['error'] or '—'))}</td>"
                f"<td><span class=\"status {escape(mcp_status)}\">{escape(mcp_status.replace('_', ' ').upper())}</span><br><small>{'Tool called: ' + ('Yes' if mcp.get('mcp_called') else 'No') if mcp else '—'}</small></td>"
                f"<td class=\"output\">{escape(str(mcp_detail))}</td></tr>"
            )
    model_rows = "".join(group_rows) or "<tr><td colspan=\"5\">No validation results were produced.</td></tr>"
    return f"""<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>OCI MCP Validation</title><style>
    :root{{font-family:Inter,system-ui,sans-serif;color:#172033;background:#f5f8fc}}body{{margin:0}}main{{max-width:1500px;margin:auto;padding:42px 24px}}h1{{margin:0 0 8px}}.meta{{color:#526176}}.summary{{background:#102a43;color:#fff;border-radius:14px;padding:18px 20px;margin:24px 0}}.filters{{display:flex;gap:10px;flex-wrap:wrap;margin:8px 0 14px}}input,select{{padding:12px;border:1px solid #b8c7da;border-radius:9px;font:inherit}}input{{flex:1 1 350px}}select{{flex:1 1 180px}}.table-wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;min-width:1100px;background:#fff;border:1px solid #d8e1ed;border-radius:12px;overflow:hidden}}th{{background:#173b63;color:#fff;text-align:left;font-size:.76rem;letter-spacing:.06em;text-transform:uppercase}}td,th{{padding:14px;border-bottom:1px solid #e5ebf2;vertical-align:top}}small{{color:#607086}}.status{{border-radius:999px;font-size:.75rem;font-weight:800;padding:5px 8px;display:inline-block}}.passed{{background:#d8f5e7;color:#096b3d}}.failed{{background:#fee2e2;color:#a61b1b}}.not_run{{background:#e8edf4;color:#526176}}.output{{max-width:480px;white-space:pre-wrap;overflow-wrap:anywhere}}.provider-row td{{background:#e9f1fa;border-bottom-color:#c8d7e8;padding-block:11px}}.provider-row:not(:first-child) td{{border-top:2px solid #b8cbe0}}.provider-toggle{{align-items:center;background:transparent;border:0;color:#173b63;cursor:pointer;display:flex;font:inherit;font-weight:800;gap:9px;padding:3px 0;text-align:left;width:100%}}.provider-toggle:focus-visible{{border-radius:5px;outline:3px solid #f5a623;outline-offset:3px}}.provider-chevron{{font-size:.72rem;transition:transform .16s ease}}.provider-toggle[aria-expanded=\"false\"] .provider-chevron{{transform:rotate(-90deg)}}.provider-summary{{color:#526176;font-size:.82rem;font-weight:500}}.provider-count{{background:#fff;border:1px solid #c8d7e8;border-radius:999px;color:#526176;font-size:.72rem;font-weight:700;margin-left:auto;padding:3px 7px;white-space:nowrap}}.model-name{{padding-left:38px;position:relative}}.model-name::before{{border-bottom:1px solid #9fb4ca;border-left:1px solid #9fb4ca;content:\"\";height:14px;left:14px;position:absolute;top:0;width:9px}}tr[hidden]{{display:none}}@media(max-width:700px){{main{{padding:24px 12px}}}}
    </style></head><body><main><p class=\"meta\">OCI Responses API · MCP calling validation</p><h1>Model validation report</h1><p class=\"meta\">Generated {escape(generated_at.strftime('%Y-%m-%d %H:%M:%S %Z'))} · MCP server: {escape(args.mcp_server_url)}</p><section class=\"summary\"><strong>{passed} of {len(results)} checks passed</strong><br>Response: {sum(item['status'] == 'passed' for item in response_results)}/{len(response_results)} · MCP: {sum(item['status'] == 'passed' for item in mcp_results)}/{len(mcp_results)}<br>MCP defaults exclude <code>olm</code> and <code>urchade</code>.</section><section class=\"filters\"><input id=\"search\" type=\"search\" placeholder=\"Search model, provider, or status…\"><select id=\"response-filter\"><option value=\"\">All response statuses</option><option value=\"passed\">Response passed</option><option value=\"failed\">Response failed</option></select><select id=\"mcp-filter\"><option value=\"\">All MCP statuses</option><option value=\"passed\">MCP passed</option><option value=\"failed\">MCP failed</option><option value=\"not_run\">MCP not run</option></select></section><section class=\"table-wrap\"><table><thead><tr><th>Model</th><th>Response</th><th>Response output / failure</th><th>MCP</th><th>MCP output / failure</th></tr></thead><tbody>{model_rows}</tbody></table></section></main><script>const search=document.querySelector('#search'),responseFilter=document.querySelector('#response-filter'),mcpFilter=document.querySelector('#mcp-filter'),groups=[...document.querySelectorAll('.provider-row')];function matches(row){{return row.dataset.search.includes(search.value.trim().toLowerCase())&&(!responseFilter.value||row.dataset.response===responseFilter.value)&&(!mcpFilter.value||row.dataset.mcp===mcpFilter.value)}}function filter(expandMatches=false){{for(const groupRow of groups){{const provider=groupRow.dataset.providerGroup,rows=[...document.querySelectorAll(`.model-row[data-provider="${{CSS.escape(provider)}}"]`)],matching=rows.filter(matches),toggle=groupRow.querySelector('.provider-toggle');if(expandMatches&&matching.length)toggle.setAttribute('aria-expanded','true');groupRow.hidden=!matching.length;for(const row of rows)row.hidden=!matching.includes(row)||toggle.getAttribute('aria-expanded')==='false'}}}}for(const groupRow of groups){{groupRow.querySelector('.provider-toggle').addEventListener('click',()=>{{const toggle=groupRow.querySelector('.provider-toggle');toggle.setAttribute('aria-expanded',String(toggle.getAttribute('aria-expanded')==='false'));filter()}})}}[search,responseFilter,mcpFilter].forEach(control=>control.addEventListener('input',()=>filter(true)));filter();</script></body></html>"""


def main() -> int:
    """Configure CLI options and tee console output to a timestamped log file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include", action="append", default=[], metavar="TERM", help="Only validate models matching name, ID, or vendor; repeatable.")
    parser.add_argument("--exclude", action="append", default=[], metavar="TERM", help="Skip models matching name, ID, or vendor; repeatable.")
    parser.add_argument("--mcp-server-url", default=DEFAULT_MCP_SERVER_URL, help="Streamable-HTTP MCP endpoint.")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Prompt that requires the MCP tool.")
    parser.add_argument("--timeout", type=float, default=90, help="Per-model request timeout in seconds.")
    parser.add_argument("--workers", type=int, default=8, help="Maximum concurrent validation requests (default: 8).")
    parser.add_argument("--log-file", type=Path, help="Optional path for the execution log; defaults to logs/.")
    args = parser.parse_args()
    run_started = datetime.now().astimezone()
    LOGS_DIR.mkdir(exist_ok=True)
    log_path = args.log_file or LOGS_DIR / f"mcp_validation_{run_started.strftime('%Y%m%d_%H%M%S')}.log"
    with log_path.open("a", encoding="utf-8") as log_file:
        original_stdout, original_stderr = sys.stdout, sys.stderr
        sys.stdout = Tee(original_stdout, log_file)
        sys.stderr = Tee(original_stderr, log_file)
        try:
            return run_validation(args, log_path)
        finally:
            sys.stdout, sys.stderr = original_stdout, original_stderr


def run_validation(args: argparse.Namespace, log_path: Path) -> int:
    """Run both validation phases after logging has been configured."""
    print(f"Execution log: {log_path}")
    try:
        settings = load_settings()
        config = oci.config.from_file(profile_name=normalize_profile(settings["cli_profile"]))
        all_models = list_models(config, settings["compartment_ocid"])
    except (RuntimeError, oci.exceptions.ConfigFileNotFound, oci.exceptions.ProfileNotFound, oci.exceptions.ServiceError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(f"Phase 1: validating Responses API output for {len(all_models)} listed model(s)")
    results = []
    response_passed = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        response_futures = [executor.submit(validate_response, config, settings["enterprise_ai_project_ocid"], model, args) for model in all_models]
        for index, (model, future) in enumerate(zip(all_models, response_futures), start=1):
            result = future.result()
            print(f"[response {index}/{len(all_models)}] {model.display_name}: {result['status']}")
            result.update(phase="Response", model_id=str(model.id), name=str(model.display_name or model.id), vendor=str(model.vendor or "—"))
            results.append(result)
            if result["status"] == "passed":
                response_passed.append(model)
    mcp_models = select_models(response_passed, args.include, args.exclude)
    print(f"Phase 2: validating MCP calls for {len(mcp_models)} response-capable model(s)")
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        mcp_futures = [executor.submit(validate_mcp, config, settings["enterprise_ai_project_ocid"], model, args) for model in mcp_models]
        for index, (model, future) in enumerate(zip(mcp_models, mcp_futures), start=1):
            result = future.result()
            print(f"[mcp {index}/{len(mcp_models)}] {model.display_name}: {result['status']}")
            result.update(phase="MCP", model_id=str(model.id), name=str(model.display_name or model.id), vendor=str(model.vendor or "—"))
            results.append(result)
    generated_at = datetime.now().astimezone()
    OUTPUTS_DIR.mkdir(exist_ok=True)
    report_path = OUTPUTS_DIR / f"mcp_validation_{generated_at.strftime('%Y%m%d_%H%M%S')}.html"
    report_path.write_text(render_html(results, generated_at, args), encoding="utf-8")
    passed = sum(result["status"] == "passed" for result in results)
    print(f"Created HTML report: {report_path} ({passed}/{len(results)} checks passed)")
    return 0 if passed == len(results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
