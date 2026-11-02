"""Generate ``contracts/``: what each provider sends its vendor, read from the code.

Each provider is built with a throwaway key and its request is built once per option value;
the contract records which wire fields each value sets, or that it is refused. ``source``,
``verified`` and ``vendor_options`` are written by hand and kept across regenerations.

    make contracts                                # regenerate the committed contracts
    make contracts PROVIDER=search/brave          # start a new one, then fill in its hand fields
    uv run python -m scripts.contracts --check    # exit 1 if a committed contract is stale
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import typing
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel, ValidationError

from web_router import registry
from web_router.constants import (
    ANSWER_ENV_BY_PROVIDER,
    AUTO_EXTRACT_KEYLESS,
    AUTO_KEYLESS,
    BROWSE_ENV_BY_PROVIDER,
    BROWSER_ENV_BY_PROVIDER,
    ENV_BY_PROVIDER,
    EXTRACT_ENV_BY_PROVIDER,
    RESEARCH_ENV_BY_PROVIDER,
    TEMPLATE_ENV_BY_PROVIDER,
)
from web_router.exceptions import WebRouterError
from web_router.types import (
    AnswerRequest,
    BrowseRequest,
    BrowserRequest,
    ExtractRequest,
    ResearchRequest,
    SearchRequest,
    TemplateRequest,
    Viewport,
)

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = ROOT / "contracts"

HAND_FIELDS = ("source", "verified", "vendor_options")

PROBE_KEY = "contract-probe-key"
"""The throwaway credential; checked to appear in no written contract."""

PROBE_ENV = {
    "BRIGHTDATA_ZONE": "contract-probe-zone",
    "OXYLABS_USERNAME": "contract-probe-user",
    "OXYLABS_PASSWORD": "contract-probe-password",
}
"""Settings a provider needs beside its key to be constructed. They replace the environment's own."""

SAMPLES: dict[str, Any] = {
    "max_results": 3,
    "include_domains": ["example.com"],
    "exclude_domains": ["example.org"],
    "country": "US",
    "locale": "en",
    "output_schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
    "records": [{"name": "Example"}],
    "proxy_country": "US",
    "profile": "profile-id",
    "timeout": 300.0,
    "idle_timeout": 60.0,
    "viewport": Viewport(width=1280, height=720),
    "start_url": "https://example.com",
    "max_steps": 10,
    "allowed_domains": ["example.com"],
}
"""The value each free-form option is probed with. Literals and booleans are probed at every value."""

ALONGSIDE: dict[str, dict[str, Any]] = {
    "persist_profile": {"profile": SAMPLES["profile"]},
    "records": {"output_schema": SAMPLES["output_schema"]},
}
"""Options that mean nothing alone, probed on top of these; only their own change is recorded."""


class _Method(typing.NamedTuple):
    model: type[BaseModel]
    names: Callable[[], list[str]]
    get_class: Callable[[str], type[Any]]
    request: Callable[..., BaseModel]
    env: dict[str, Any]
    keyless: tuple[tuple[str, dict[str, Any]], ...]


METHODS: dict[str, _Method] = {
    "search": _Method(
        SearchRequest,
        registry.available_providers,
        registry.get_provider_class,
        lambda **kw: SearchRequest(query="probe query", **kw),
        ENV_BY_PROVIDER,
        AUTO_KEYLESS,
    ),
    "extract": _Method(
        ExtractRequest,
        registry.available_extract_providers,
        registry.get_extract_provider_class,
        lambda **kw: ExtractRequest(urls=["https://example.com"], **kw),
        EXTRACT_ENV_BY_PROVIDER,
        AUTO_EXTRACT_KEYLESS,
    ),
    "research": _Method(
        ResearchRequest,
        registry.available_research_providers,
        registry.get_research_provider_class,
        lambda **kw: ResearchRequest(query="probe query", **kw),
        RESEARCH_ENV_BY_PROVIDER,
        (),
    ),
    "answer": _Method(
        AnswerRequest,
        registry.available_answer_providers,
        registry.get_answer_provider_class,
        lambda **kw: AnswerRequest(query="probe query", **kw),
        ANSWER_ENV_BY_PROVIDER,
        (),
    ),
    "browser": _Method(
        BrowserRequest,
        registry.available_browser_providers,
        registry.get_browser_provider_class,
        lambda **kw: BrowserRequest(**kw),
        BROWSER_ENV_BY_PROVIDER,
        (),
    ),
    "template": _Method(
        TemplateRequest,
        registry.available_template_providers,
        registry.get_template_provider_class,
        lambda **kw: TemplateRequest(template="probe-template", inputs={"url": "https://example.com"}, **kw),
        TEMPLATE_ENV_BY_PROVIDER,
        (),
    ),
    "browse": _Method(
        BrowseRequest,
        registry.available_browse_providers,
        registry.get_browse_provider_class,
        lambda **kw: BrowseRequest(task="probe task", **kw),
        BROWSE_ENV_BY_PROVIDER,
        (),
    ),
}

WIRE_PARTS = {"json": "body", "params": "query", "headers": "headers"}
"""``_call_kwargs`` keys -> where on the request they land."""


def _credentials(spec: _Method, name: str) -> list[str]:
    env = spec.env[name]
    return [env] if isinstance(env, str) else list(env or ())


def _options(model: type[BaseModel]) -> list[str]:
    return [name for name in model.model_fields if name not in model.always_sent]  # type: ignore[attr-defined]


def _values(model: type[BaseModel], option: str) -> list[Any]:
    annotation = model.model_fields[option].annotation
    for arg in [a for a in get_args(annotation) if a is not type(None)] or [annotation]:
        if get_origin(arg) is Literal:
            return list(get_args(arg))
        if arg is bool:
            return [True, False]
    if option not in SAMPLES:
        raise SystemExit(f"{model.__name__}.{option} has no sample value: add one to SAMPLES in {__file__}")
    return [SAMPLES[option]]


def _label(option: str, value: Any) -> str:
    """``sample`` for an option's sample; any other value by itself, so no two probes share a label."""
    if value is SAMPLES.get(option):
        return "sample"
    return value if isinstance(value, str) else json.dumps(value)


def _flatten(payload: Any, prefix: str = "") -> dict[str, Any]:
    """``{"a": {"b": 1}}`` -> ``{"a.b": 1}``, so a diff names the exact field that moved."""
    if not isinstance(payload, dict) or not payload:
        return {prefix: payload} if prefix else {}
    flat: dict[str, Any] = {}
    for key, value in payload.items():
        # A key holding a dot is quoted, so {"a.b": 1} and {"a": {"b": 1}} never read the same.
        segment = json.dumps(key) if "." in str(key) else str(key)
        for path, leaf in _flatten(value, f"{prefix}.{segment}" if prefix else segment).items():
            if path in flat:
                raise SystemExit(f"two wire fields flatten to {path!r}")
            flat[path] = leaf
    return flat


def _default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump()
    # Never str(): a default repr carries an address, and the check would fail at random.
    raise TypeError(f"{type(value).__name__} in a payload has no stable JSON form; teach _default to write it")


def _payload(provider: Any, method: str, request: BaseModel) -> dict[str, Any]:
    """The request as sent: through ``_call_kwargs`` where a provider has one, since that is where
    a query string or header joins the body (Jina Reader's format is a header)."""
    provider.validate(request)
    args = (request, request.urls) if method == "extract" else (request,)  # type: ignore[attr-defined]
    if hasattr(provider, "_call_kwargs"):
        sent = {WIRE_PARTS[key]: value for key, value in provider._call_kwargs(*args).items()}
    else:
        sent = {"body": provider._build_payload(*args)}
    return _flatten(json.loads(json.dumps(sent, sort_keys=True, default=_default)))


def _probe(provider: Any, method: str, option: str, value: Any) -> dict[str, Any]:
    make = METHODS[method].request
    alongside = ALONGSIDE.get(option, {})
    try:
        before = _payload(provider, method, make(**alongside))
    except (WebRouterError, ValidationError) as exc:
        raise SystemExit(
            f"{method}/{provider.name}: the base request for {option!r} is refused; fix ALONGSIDE"
        ) from exc
    try:
        after = _payload(provider, method, make(**alongside, **{option: value}))
    except (WebRouterError, ValidationError) as exc:
        return {"refused": type(exc).__name__}
    changed = {key: value for key, value in after.items() if before.get(key, ...) != value}
    changed |= {key: "<removed>" for key in before if key not in after}
    # No change on the wire: the value is sent anyway, or applied after the response
    # (Parallel MCP trims max_results itself). Said in words, so it never reads as dropped.
    return dict(sorted(changed.items())) or {"unchanged": "no wire change"}


def _transport(cls: type) -> dict[str, Any]:
    from web_router.providers._http import _HttpProvider
    from web_router.providers._mcp import McpSearchMixin

    if issubclass(cls, McpSearchMixin):
        return {"kind": "mcp", "url": cls.base_url, "tool": cls.mcp_tool}  # type: ignore[attr-defined]
    if not issubclass(cls, _HttpProvider):
        return {"kind": "sdk"}
    paths = {
        name: getattr(cls, name)
        for name in sorted(dir(cls))
        if name.endswith(("_path", "_url")) and name != "base_url" and isinstance(getattr(cls, name), str)
    }
    if not paths:
        raise SystemExit(f"{cls.__qualname__}: no endpoint; declare it as a *_path class attribute")
    transport: dict[str, Any] = {"kind": "http", "base_url": cls.base_url, "paths": paths}
    # The class verb is what a search or an extract is sent with; other calls choose theirs.
    if "search_path" in paths or "extract_path" in paths or "answer_path" in paths:
        transport["method"] = cls.http_method
    transport["auth_header"] = cls.api_key_header if cls.env_api_key else None
    return transport


def _keyless(spec: _Method, name: str) -> str | None:
    if not _credentials(spec, name):
        return "always"
    for member, kwargs in spec.keyless:
        if member == name:
            return "through auto, " + ", ".join(f"{key}={value!r}" for key, value in kwargs.items())
    return None


def generate(method: str, name: str) -> dict[str, Any]:
    """The generated part of one contract."""
    spec = METHODS[method]
    cls = spec.get_class(name)
    provider = cls(api_key=PROBE_KEY)
    try:
        options = _options(spec.model)
        contract: dict[str, Any] = {
            "provider": name,
            "method": method,
            "class": f"{cls.__module__}.{cls.__qualname__}",
            "transport": _transport(cls),
            "credentials": _credentials(spec, name),
            "keyless": _keyless(spec, name),
            "always_sent": dict(sorted(_payload(provider, method, spec.request()).items())),
            "options": {
                option: {
                    _label(option, value): _probe(provider, method, option, value)
                    for value in _values(spec.model, option)
                }
                for option in options
                if option in cls.supported_parameters
            },
            "not_mapped": sorted(option for option in options if option not in cls.supported_parameters),
        }
    finally:
        provider.close()
    if method == "extract":
        contract["default_format"] = cls.default_format
        contract["batches_urls"] = cls.batches_urls
    return contract


def contract_path(method: str, name: str) -> Path:
    return CONTRACTS_DIR / method / f"{name}.json"


def render(method: str, name: str) -> str:
    """The contract as committed: generated fields, plus the hand-written ones the file already has."""
    contract = generate(method, name)
    path = contract_path(method, name)
    existing = json.loads(path.read_text()) if path.is_file() else {}
    for field in HAND_FIELDS:
        if field in existing:
            contract[field] = existing[field]
    if "verified" in contract:
        dropped = sorted(set(contract["verified"]) - set(contract["options"]))
        if dropped:
            print(f"{method}/{name}: no longer maps {', '.join(dropped)}; dropping its verified entry", file=sys.stderr)
        contract["verified"] = {k: v for k, v in contract["verified"].items() if k in contract["options"]}
    text = json.dumps(contract, indent=2, ensure_ascii=False) + "\n"
    if PROBE_KEY in text:
        raise SystemExit(f"{method}/{name}: the probe credential reached the payload")
    return text


def providers() -> Iterator[tuple[str, str]]:
    """Every built-in (method, provider) that talks to a vendor; ``auto`` and ``local`` build no payload."""
    for method, spec in METHODS.items():
        for name in spec.names():
            if name in spec.env and hasattr(spec.get_class(name), "_build_payload"):
                yield method, name


def expected(new: Iterable[str] = ()) -> dict[Path, str]:
    """The committed contracts as the code says they should read, plus any ``method/provider`` in ``new``.

    A contract is committed once its hand-written fields are filled, so only those are regenerated.
    """
    known = set(providers())
    unknown = sorted(key for key in new if tuple(key.split("/", 1)) not in known)
    if unknown:
        raise SystemExit(f"no built-in provider: {', '.join(unknown)} (expected method/provider, e.g. search/brave)")
    wanted = [(m, n) for m, n in known if contract_path(m, n).is_file() or f"{m}/{n}" in new]
    saved = {key: os.environ.get(key) for key in PROBE_ENV}
    os.environ.update(PROBE_ENV)
    try:
        return {contract_path(method, name): render(method, name) for method, name in sorted(wanted)}
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def stale(files: dict[Path, str]) -> list[str]:
    problems = [
        f"{p.relative_to(ROOT)} is out of date" for p, t in files.items() if not p.is_file() or p.read_text() != t
    ]
    problems += [
        f"{p.relative_to(ROOT)} has no provider behind it" for p in CONTRACTS_DIR.glob("*/*.json") if p not in files
    ]
    return sorted(problems)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit 1 if a committed contract differs from the code")
    parser.add_argument("--new", action="append", default=[], metavar="METHOD/PROVIDER", help="start a contract")
    args = parser.parse_args()
    files = expected(args.new)
    if args.check:
        problems = stale(files)
        print(
            "\n".join([*problems, "Run `make contracts` and commit the result."] if problems else []), file=sys.stderr
        )
        raise SystemExit(1 if problems else 0)
    for path in CONTRACTS_DIR.glob("*/*.json"):
        if path not in files:
            path.unlink()
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    print(f"Wrote {len(files)} contracts to {CONTRACTS_DIR.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
