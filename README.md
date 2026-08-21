# Enterprise AI validations

Utilities for discovering the OCI Generative AI models available in the configured compartment and validating OCI's OpenAI-compatible Responses API with remote MCP tool calling.

## Prerequisites

- Python environment at `.venv` with `oci` and `PyYAML` installed.
- OCI CLI credentials in `~/.oci/config`; credentials are not stored in this repository.
- `config.yaml` containing the OCI CLI profile, compartment OCID, and Enterprise AI project OCID. This local file is intentionally excluded from Git; create it with the required values before running the inventory or MCP validators.
- Network access to OCI. In this environment, OCI requests must run without the corporate proxy variables.

## Scripts

### `list_generative_ai_models.py`

Lists the Generative AI models visible in the configured compartment.

```bash
env -u http_proxy -u https_proxy \
  ./.venv/bin/python list_generative_ai_models.py
```

Options:

- `--output html` (default): writes a timestamped report to `outputs/`.
- `--output text`: writes a compact terminal list instead.

The HTML report supports full-text search, vendor and capability filters, model-ID copying, responsive layout, and visual capability/state badges.

### `validate_mcp_responses.py`

Runs a two-phase validation against the OCI OpenAI-compatible `/responses` endpoint.

1. **Response validation**: tests every model returned by `list_models` for non-empty text output.
2. **MCP validation**: tests only models that passed phase 1, using the configured Streamable HTTP MCP server. The default DeepWiki prompt explicitly requires an MCP call.

By default, MCP validation excludes providers `olm` and `urchade`. Use `--include` to limit MCP validation to matching model name, ID, or provider, or `--exclude` to add exclusions. Response validation always evaluates the full listed model inventory.

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY \
  ./.venv/bin/python -u validate_mcp_responses.py --workers 128 --timeout 12
```

Options:

- `--include TERM`: repeatable MCP candidate selector.
- `--exclude TERM`: repeatable MCP candidate exclusion.
- `--mcp-server-url URL`: Streamable HTTP MCP endpoint; defaults to `https://mcp.deepwiki.com/mcp`.
- `--prompt TEXT`: MCP-required prompt.
- `--timeout SECONDS`: timeout per OCI request.
- `--workers COUNT`: concurrent validation requests; default is 8.
- `--log-file PATH`: override the timestamped log location.

The combined HTML report groups models into collapsible provider sections. Each provider header shows Response and MCP pass counts, while model rows retain separate **Response** and **MCP** statuses, HTTP details, and output/failure text. Search and status filters automatically expand matching provider sections. A model with no MCP result is marked **Not run**.

### `validate_dedicated_openai_response.py`

Validates a model endpoint hosted on an OCI Generative AI dedicated AI cluster through the OpenAI-compatible `/responses` API. The endpoint OCID is sent as the request's `model` value, and the request is signed with the selected OCI CLI profile.

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY \
  ./.venv/bin/python validate_dedicated_openai_response.py \
  --endpoint-ocid '<endpoint-ocid>' \
  --region 'us-chicago-1' \
  --project-ocid '<generative-ai-project-ocid>'
```

Required options:

- `--endpoint-ocid`: OCI Generative AI endpoint OCID hosted on the dedicated cluster.
- `--region`: region that contains that endpoint.

Optional options:

- `--project-ocid`: Generative AI project OCID. Alternatively, set `OCI_GENAI_PROJECT_OCID`.
- `--profile`: OCI CLI profile used for IAM signing; defaults to `DEFAULT`.
- `--config-file`: alternate OCI CLI configuration path.
- `--prompt`: validation prompt.
- `--timeout`: request timeout in seconds; defaults to 90.

The script exits with code 0 only if OCI returns HTTP 200 with non-empty output text. It uses the local `.venv` automatically when invoked with another Python interpreter that lacks the OCI SDK.

## Generated artifacts

- `outputs/generative_ai_models_*.html`: model inventory reports.
- `outputs/mcp_validation_*.html`: combined Response and MCP validation reports.
- `logs/mcp_validation_*.log`: live validator console output and errors.

## OCI tool coverage

The current validator exercises the Responses API plus remote MCP Calling (`tools[].type: mcp`). OCI also supports File Search, Code Interpreter, and Function Calling through the Responses API. These require separate fixture and lifecycle handling: File Search needs temporary uploaded files/vector stores, Code Interpreter can create OCI-managed containers, and Function Calling requires a client-side function-call-output follow-up. They are not yet part of `validate_mcp_responses.py`.

See Oracle's [Tools documentation](https://docs.oracle.com/en-us/iaas/Content/generative-ai/tool-support.htm) and [Responses API documentation](https://docs.oracle.com/en-us/iaas/Content/generative-ai/responses-api.htm) for the currently supported OCI tool types and model/region constraints.
