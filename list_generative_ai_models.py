#!/usr/bin/env python3
"""Generate an inventory of OCI Generative AI models in the configured compartment.

Reads the OCI CLI profile and compartment OCID from the local ``config.yaml`` file,
uses OCI CLI credentials for authentication, and writes either an HTML report or a
plain-text inventory. Generated reports are stored in ``outputs/``.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from html import escape
from pathlib import Path
import sys

import oci
import yaml


CONFIG_PATH = Path(__file__).with_name("config.yaml")
OUTPUTS_DIR = Path(__file__).with_name("outputs")


def load_settings() -> dict[str, str]:
    """Load non-secret OCI settings maintained with this validation project."""
    try:
        with CONFIG_PATH.open(encoding="utf-8") as config_file:
            settings = yaml.safe_load(config_file) or {}
        oci_settings = settings["oci"]
        return {
            "cli_profile": oci_settings["cli_profile"],
            "compartment_ocid": oci_settings["compartment_ocid"],
        }
    except (FileNotFoundError, KeyError, TypeError, yaml.YAMLError) as error:
        raise RuntimeError(f"Could not load OCI settings from {CONFIG_PATH}: {error}") from error


def render_html(models: list[object], generated_at: datetime) -> str:
    """Render a searchable, standalone inventory of the models returned by OCI."""
    rows = []
    vendors = set()
    capabilities_seen = set()
    for model in models:
        name = str(model.display_name or "Unnamed model")
        model_id = str(model.id or "—")
        vendor = str(model.vendor or "—")
        model_type = str(model.type or "—")
        version = str(model.version or "—")
        lifecycle_state = str(model.lifecycle_state or "—")
        long_term_supported = (
            "Yes" if model.is_long_term_supported is True
            else "No" if model.is_long_term_supported is False
            else "—"
        )
        capabilities = [str(capability) for capability in (model.capabilities or [])]
        capability_text = ", ".join(capabilities) or "—"
        vendors.add(vendor)
        capabilities_seen.update(capabilities)
        badges = "".join(f"<span class=\"badge\">{escape(capability)}</span>" for capability in capabilities)
        rows.append(
            f"<tr data-search=\"{escape(' '.join((name, model_id, vendor, model_type, version, lifecycle_state, capability_text)).lower())}\" "
            f"data-vendor=\"{escape(vendor)}\" data-capabilities=\"{escape('|'.join(capabilities))}\">"
            f"<td class=\"model-name\">{escape(name)}<span class=\"version\">v{escape(version)}</span></td>"
            f"<td>{escape(vendor)}</td><td><span class=\"type\">{escape(model_type)}</span></td>"
            f"<td class=\"capabilities\">{badges or '—'}</td>"
            f"<td><span class=\"state\">{escape(lifecycle_state)}</span></td>"
            f"<td>{long_term_supported}</td>"
            f"<td class=\"id-cell\"><code title=\"{escape(model_id)}\">{escape(model_id)}</code>"
            f"<button class=\"copy-id\" type=\"button\" data-id=\"{escape(model_id)}\" aria-label=\"Copy model ID for {escape(name)}\">Copy</button></td></tr>"
        )

    vendor_options = "".join(f"<option value=\"{escape(vendor)}\">{escape(vendor)}</option>" for vendor in sorted(vendors))
    capability_options = "".join(f"<option value=\"{escape(capability)}\">{escape(capability)}</option>" for capability in sorted(capabilities_seen))
    model_rows = "".join(rows) or "<tr><td colspan=\"7\" class=\"empty\">No Generative AI models are available in this compartment.</td></tr>"
    timestamp = generated_at.strftime("%Y-%m-%d %H:%M:%S %Z")
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>OCI Generative AI Models</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, sans-serif; color: #162238; background: #f4f7fb; }}
    * {{ box-sizing: border-box; }} body {{ margin: 0; background: linear-gradient(135deg, #eef4ff, #f8fafc 45%, #eefbf7); }}
    main {{ max-width: 1440px; margin: auto; padding: 48px 24px 72px; }}
    header {{ margin-bottom: 24px; }} h1 {{ margin: 0 0 8px; font-size: clamp(1.8rem, 4vw, 2.5rem); letter-spacing: -.035em; }}
    .eyebrow {{ color: #176b58; font-size: .76rem; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; margin: 0 0 10px; }}
    .meta {{ color: #526176; margin: 0; }} .toolbar {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: center; background: #fff; border: 1px solid #d9e2ef; border-radius: 16px; padding: 14px; box-shadow: 0 12px 30px #273c5c12; margin-bottom: 16px; }}
    input, select {{ min-height: 42px; border: 1px solid #bdc9da; border-radius: 9px; background: #fff; color: #162238; font: inherit; padding: 0 12px; }} input {{ flex: 1 1 320px; }} select {{ flex: 0 1 190px; }} input:focus, select:focus {{ outline: 3px solid #6ea8fe66; border-color: #2673dd; }}
    #result-count {{ color: #526176; font-size: .9rem; font-weight: 700; margin-left: auto; white-space: nowrap; }} .table-wrap {{ overflow-x: auto; border: 1px solid #d9e2ef; border-radius: 16px; background: #fff; box-shadow: 0 12px 30px #273c5c12; }}
    table {{ border-collapse: collapse; width: 100%; min-width: 1100px; text-align: left; }} th {{ background: #172b4d; color: #f8fbff; font-size: .72rem; letter-spacing: .06em; padding: 14px 16px; text-transform: uppercase; }} td {{ border-top: 1px solid #e6ebf2; padding: 15px 16px; vertical-align: top; font-size: .9rem; }} tbody tr:hover {{ background: #f1f7ff; }} .model-name {{ color: #104e9e; font-weight: 800; min-width: 200px; overflow-wrap: anywhere; }} .version {{ color: #64748b; display: block; font-size: .78rem; font-weight: 600; margin-top: 4px; }}
    .badge, .type, .state {{ border-radius: 999px; display: inline-block; font-size: .71rem; font-weight: 800; line-height: 1; padding: 6px 8px; }} .badge {{ background: #e6f4ff; color: #075985; margin: 0 4px 4px 0; }} .type {{ background: #eee8ff; color: #5b32a0; }} .state {{ background: #def7ec; color: #087443; }} .id-cell {{ max-width: 330px; }} code {{ color: #3d4b60; font-size: .74rem; overflow-wrap: anywhere; }} .copy-id {{ background: transparent; border: 0; color: #1463be; cursor: pointer; font: inherit; font-size: .75rem; font-weight: 800; margin: 7px 0 0; padding: 0; }} .copy-id:hover {{ color: #0c3d78; text-decoration: underline; }} .empty {{ color: #526176; padding: 32px; text-align: center; }} tr[hidden] {{ display: none; }}
    @media (max-width: 650px) {{ main {{ padding: 32px 16px 48px; }} .toolbar {{ padding: 12px; }} input, select {{ flex-basis: 100%; width: 100%; }} #result-count {{ margin-left: 0; }} }}
  </style>
</head>
<body><main>
  <header><p class=\"eyebrow\">OCI Generative AI</p><h1>Available models</h1><p class=\"meta\">Generated {escape(timestamp)}</p></header>
  <section class=\"toolbar\" aria-label=\"Model filters\"><input id=\"search\" type=\"search\" placeholder=\"Search name, vendor, capability, version, or model ID…\" autofocus><select id=\"vendor\"><option value=\"\">All vendors</option>{vendor_options}</select><select id=\"capability\"><option value=\"\">All capabilities</option>{capability_options}</select><span id=\"result-count\">{len(models)} models</span></section>
  <section class=\"table-wrap\"><table><thead><tr><th>Model</th><th>Vendor</th><th>Type</th><th>Capabilities</th><th>Status</th><th>LTS</th><th>Model ID</th></tr></thead><tbody id=\"models\">{model_rows}</tbody></table></section>
</main></body>
<script>
  const search = document.querySelector('#search');
  const vendor = document.querySelector('#vendor');
  const capability = document.querySelector('#capability');
  const count = document.querySelector('#result-count');
  const rows = [...document.querySelectorAll('#models tr[data-search]')];
  function filterModels() {{
    const query = search.value.trim().toLowerCase();
    let visible = 0;
    rows.forEach((row) => {{
      const matches = (!query || row.dataset.search.includes(query)) && (!vendor.value || row.dataset.vendor === vendor.value) && (!capability.value || row.dataset.capabilities.split('|').includes(capability.value));
      row.hidden = !matches;
      if (matches) visible += 1;
    }});
    count.textContent = `${{visible}} ${{visible === 1 ? 'model' : 'models'}}`;
  }}
  [search, vendor, capability].forEach((control) => control.addEventListener('input', filterModels));
  document.addEventListener('click', async (event) => {{
    const button = event.target.closest('.copy-id');
    if (!button) return;
    await navigator.clipboard.writeText(button.dataset.id);
    button.textContent = 'Copied'; setTimeout(() => {{ button.textContent = 'Copy'; }}, 1400);
  }});
</script>
</html>
"""


def print_text(models: list[object]) -> None:
    """Print a compact terminal-friendly inventory of the discovered models."""
    if not models:
        print("No Generative AI models are available in this compartment.")
        return

    print(f"Available Generative AI models: {len(models)}\n")
    for model in models:
        print(f"Name: {model.display_name}")
        print(f"ID:   {model.id}")
        print(f"Type: {model.type}")
        print(f"Vendor/version: {model.vendor} / {model.version}\n")


def main() -> int:
    """Parse CLI options, list accessible models, and emit the requested report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        choices=("html", "text"),
        default="html",
        help="Report format (default: html).",
    )
    args = parser.parse_args()

    try:
        settings = load_settings()
        profile_name = settings["cli_profile"]
        # OCI's generated config calls its default profile "DEFAULT". Accept the
        # lowercase form in this project's config as a convenience.
        if profile_name.lower() == "default":
            profile_name = "DEFAULT"
        oci_config = oci.config.from_file(profile_name=profile_name)
        client = oci.generative_ai.GenerativeAiClient(oci_config)
        response = oci.pagination.list_call_get_all_results(
            client.list_models,
            compartment_id=settings["compartment_ocid"],
        )
    except (
        RuntimeError,
        oci.exceptions.ConfigFileNotFound,
        oci.exceptions.ProfileNotFound,
        oci.exceptions.ServiceError,
    ) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    models = sorted(response.data, key=lambda model: (model.display_name or "", model.id))
    if args.output == "text":
        print_text(models)
        return 0

    generated_at = datetime.now().astimezone()
    OUTPUTS_DIR.mkdir(exist_ok=True)
    report_path = OUTPUTS_DIR / f"generative_ai_models_{generated_at.strftime('%Y%m%d_%H%M%S')}.html"
    report_path.write_text(render_html(models, generated_at), encoding="utf-8")
    print(f"Created HTML report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
