#!/usr/bin/env python3
"""Validate an OCI Generative AI dedicated endpoint through the Responses API.

The required endpoint OCID is supplied as the OpenAI-compatible ``model`` value,
and the region selects OCI's regional ``/openai/v1/responses`` URL. Requests are
signed with an OCI CLI profile; a Generative AI project OCID can be supplied when
required by the target environment.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

try:
    import oci
    from oci._vendor import requests
except ModuleNotFoundError as error:
    project_python = Path(__file__).with_name(".venv") / "bin" / "python"
    if error.name == "oci" and project_python.is_file() and Path(sys.prefix).resolve() != project_python.parent.parent.resolve():
        os.execv(str(project_python), [str(project_python), __file__, *sys.argv[1:]])
    if error.name == "oci":
        raise SystemExit(
            "OCI Python SDK is required. Install it with: python -m pip install oci\n"
            "Or run this script with the project virtual environment: ./.venv/bin/python validate_dedicated_openai_response.py"
        ) from error
    raise


def response_url(region: str) -> str:
    """Return the OCI OpenAI-compatible Responses API URL for a region."""
    return f"https://inference.generativeai.{region}.oci.oraclecloud.com/openai/v1/responses"


def extract_output_text(data: dict[str, Any]) -> str:
    """Extract text from an OpenAI-compatible Responses API payload."""
    texts = []
    for item in data.get("output", []) or []:
        for content in item.get("content", []) or []:
            if content.get("type") == "output_text" and content.get("text"):
                texts.append(str(content["text"]))
    return "\n".join(texts)


def parse_args() -> argparse.Namespace:
    """Parse the dedicated-endpoint location, authentication, and request options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--endpoint-ocid",
        required=True,
        help="OCID of the OCI Generative AI endpoint hosted on the dedicated AI cluster.",
    )
    parser.add_argument(
        "--region",
        required=True,
        help="OCI region containing the endpoint, for example us-chicago-1.",
    )
    parser.add_argument(
        "--project-ocid",
        default=os.environ.get("OCI_GENAI_PROJECT_OCID"),
        help="Optional Generative AI project OCID (or set OCI_GENAI_PROJECT_OCID).",
    )
    parser.add_argument(
        "--profile",
        default="DEFAULT",
        help="OCI CLI profile used for request signing (default: DEFAULT).",
    )
    parser.add_argument(
        "--config-file",
        help="Optional path to the OCI CLI config file (default: ~/.oci/config).",
    )
    parser.add_argument(
        "--prompt",
        default="Reply with exactly: dedicated endpoint validation passed.",
        help="Input sent to the endpoint.",
    )
    parser.add_argument("--timeout", type=float, default=90, help="Request timeout in seconds (default: 90).")
    return parser.parse_args()


def main() -> int:
    """Submit one signed validation request and return a shell-friendly status code."""
    args = parse_args()
    if args.timeout <= 0:
        print("Error: --timeout must be greater than zero.", file=sys.stderr)
        return 2

    try:
        config_options = {"profile_name": args.profile}
        if args.config_file:
            config_options["file_location"] = args.config_file
        config = oci.config.from_file(**config_options)
        signer = oci.signer.Signer(
            tenancy=config["tenancy"],
            user=config["user"],
            fingerprint=config["fingerprint"],
            private_key_file_location=config["key_file"],
            pass_phrase=config.get("pass_phrase"),
        )
    except (KeyError, oci.exceptions.ConfigFileNotFound, oci.exceptions.ProfileNotFound) as error:
        print(f"Error loading OCI profile {args.profile!r}: {error}", file=sys.stderr)
        return 2

    headers = {"Content-Type": "application/json"}
    if args.project_ocid:
        headers["OpenAI-Project"] = args.project_ocid
    payload = {"model": args.endpoint_ocid, "input": args.prompt, "store": False}
    url = response_url(args.region)
    print(f"Validating dedicated endpoint: {args.endpoint_ocid}")
    print(f"Responses API URL: {url}")

    try:
        response = requests.post(url, json=payload, auth=signer, headers=headers, timeout=args.timeout)
        try:
            data = response.json() if response.content else {}
        except ValueError:
            data = {"raw_response": response.text}
    except requests.RequestException as error:
        print(f"FAILED: request error: {error}", file=sys.stderr)
        return 2

    output = extract_output_text(data)
    if response.status_code == 200 and output:
        print("PASSED: received a non-empty Responses API text output.")
        print(output)
        return 0

    error = data.get("error", {}) if isinstance(data, dict) else {}
    detail = error.get("message") if isinstance(error, dict) else None
    detail = detail or json.dumps(data, ensure_ascii=False)
    print(f"FAILED: HTTP {response.status_code}: {detail}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
