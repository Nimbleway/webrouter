"""Generate ``contracts/``: what each provider sends its vendor, read from the code.

One JSON file per method and provider. The generator builds each provider with a
throwaway key, builds its request once per option value -- body, query string and
headers -- and records which wire fields that value adds, changes or removes. Nothing is read from a
table by hand, so a mapping that lives in ``constants.py``, a class attribute or
payload code is captured the same way.

A contract also holds fields only a person can fill -- where the vendor documents
the API, how each mapped option was verified, and the vendor's own options that
web-router does not map. Regenerating keeps them; a new contract gets ``TODO``.

    make contracts                          # rewrite contracts/
    uv run python -m scripts.contracts --check   # fail if contracts/ is stale
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import types
import typing
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel, ValidationError

from web_router import registry
from web_router.constants import (
    AUTO_EXTRACT_KEYLESS,
    AUTO_KEYLESS,
    BROWSE_ENV_BY_PROVIDER,
    BROWSER_ENV_BY_PROVIDER,
    ENV_BY_PROVIDER,
    EXTRACT_ENV_BY_PROVIDER,
    RESEARCH_ENV_BY_PROVIDER,
)
from web_router.exceptions import WebRouterError
from web_router.types import (
    BrowseRequest,
    BrowserRequest,
    ExtractRequest,
    ResearchRequest,
    SearchRequest,
    Viewport,
)

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = ROOT / "contracts"

TODO = "TODO"

PROBE_KEY = "contract-probe-key"
"""The throwaway credential every provider is built with. Never a real key, and
checked to appear nowhere in a written contract."""

PROBE_ENV = {
    "BRIGHTDATA_ZONE": "contract-probe-zone",
    "OXYLABS_USERNAME": "contract-probe-user",
    "OXYLABS_PASSWORD": "contract-probe-password",
}
"""Values a provider needs beside its key just to be constructed."""

HAND_FIELDS = ("source", "verified", "vendor_options")
"""Fields a person owns. Regenerating never overwrites them."""

SAMPLES: dict[str, list[Any]] = {
    "max_results": [3],
    "include_domains": [["example.com"]],
    "exclude_domains": [["example.org"]],
    "country": ["US"],
    "locale": ["en"],
    "output_schema": [{"type": "object", "properties": {"answer": {"type": "string"}}}],
    "records": [[{"name": "Example"}]],
    "proxy_country": ["US"],
    "profile": ["profile-id"],
    "timeout": [300.0],
    "idle_timeout": [60.0],
    "viewport": [Viewport(width=1280, height=720)],
    "start_url": ["https://example.com"],
    "max_steps": [10],
    "allowed_domains": [["example.com"]],
}
"""One representative value per free-form option. A literal or a boolean is probed
at every value instead, so it needs no entry here; a new free-form option does,
and the generator stops until it has one."""

PREREQUISITES: dict[str, dict[str, Any]] = {"persist_profile": {"profile": "profile-id"}}
"""Options that mean nothing alone. Each is probed on top of these, and only its own
change is recorded."""


class _Method(typing.NamedTuple):
    names: Callable[[], list[str]]
    get_class: Callable[[str], type[Any]]
    request: Callable[..., BaseModel]
    env: Callable[[str], tuple[str, ...]]
    keyless: tuple[tuple[str, dict[str, Any]], ...]
    builtin: frozenset[str]
    """The built-in providers: each method's credential table lists exactly those."""


def _search_env(name: str) -> tuple[str, ...]:
    env_var = ENV_BY_PROVIDER[name]
    return (env_var,) if env_var else ()


METHODS: dict[str, _Method] = {
    "search": _Method(
        registry.available_providers,
        registry.get_provider_class,
        lambda **kw: SearchRequest(query="probe query", **kw),
        _search_env,
        AUTO_KEYLESS,
        frozenset(ENV_BY_PROVIDER),
    ),
    "extract": _Method(
        registry.available_extract_providers,
        registry.get_extract_provider_class,
        lambda **kw: ExtractRequest(urls=["https://example.com"], **kw),
        EXTRACT_ENV_BY_PROVIDER.__getitem__,
        AUTO_EXTRACT_KEYLESS,
        frozenset(EXTRACT_ENV_BY_PROVIDER),
    ),
    "research": _Method(
        registry.available_research_providers,
        registry.get_research_provider_class,
        lambda **kw: ResearchRequest(query="probe query", **kw),
        RESEARCH_ENV_BY_PROVIDER.__getitem__,
        (),
        frozenset(RESEARCH_ENV_BY_PROVIDER),
    ),
    "browser": _Method(
        registry.available_browser_providers,
        registry.get_browser_provider_class,
        lambda **kw: BrowserRequest(**kw),
        BROWSER_ENV_BY_PROVIDER.__getitem__,
        (),
        frozenset(BROWSER_ENV_BY_PROVIDER),
    ),
    "browse": _Method(
        registry.available_browse_providers,
        registry.get_browse_provider_class,
        lambda **kw: BrowseRequest(task="probe task", **kw),
        BROWSE_ENV_BY_PROVIDER.__getitem__,
        (),
        frozenset(BROWSE_ENV_BY_PROVIDER),
    ),
}

REQUEST_MODELS: dict[str, type[BaseModel]] = {
    "search": SearchRequest,
    "extract": ExtractRequest,
    "research": ResearchRequest,
    "browser": BrowserRequest,
    "browse": BrowseRequest,
}


def _options(model: type[BaseModel]) -> list[str]:
    """The model's caller-facing options: every field but the ones each provider always handles."""
    return [name for name in model.model_fields if name not in model.always_sent]  # type: ignore[attr-defined]


def _values(model: type[BaseModel], option: str) -> list[Any]:
    """Every value of a literal or a boolean, else the one sample in :data:`SAMPLES`."""
    annotation = model.model_fields[option].annotation
    args = [arg for arg in get_args(annotation) if arg is not type(None)]
    for arg in args or [annotation]:
        if get_origin(arg) is Literal:
            return list(get_args(arg))
        if arg is bool:
            return [True, False]
    if option not in SAMPLES:
        raise SystemExit(f"{model.__name__}.{option} has no sample value: add one to SAMPLES in {__file__}")
    return SAMPLES[option]


def _label(option: str, value: Any) -> str:
    """A free-form option's only sample as ``sample``; anything else by its value, so no two collide."""
    if len(SAMPLES.get(option, [])) == 1 and SAMPLES[option][0] is value:
        return "sample"
    if isinstance(value, BaseModel):
        value = value.model_dump()
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)


def _segment(key: Any) -> str:
    """A key as one path segment. A key that holds a dot is quoted, so ``{"a.b": 1}``
    and ``{"a": {"b": 1}}`` never flatten to the same path."""
    key = str(key)
    return json.dumps(key) if "." in key or key.startswith('"') else key


def _flatten(payload: Any, prefix: str = "") -> dict[str, Any]:
    """``{"a": {"b": 1}}`` -> ``{"a.b": 1}``, so a diff names the exact field that moved."""
    if not isinstance(payload, dict) or not payload:
        return {prefix: payload} if prefix else {}
    flat: dict[str, Any] = {}
    for key, value in payload.items():
        for path, leaf in _flatten(value, f"{prefix}.{_segment(key)}" if prefix else _segment(key)).items():
            if path in flat:
                raise SystemExit(f"two wire fields flatten to {path!r}; make _segment tell them apart")
            flat[path] = leaf
    return flat


def _jsonable(value: Any) -> Any:
    """Round-trip through JSON so tuples, models and the like compare as they are written."""
    return json.loads(json.dumps(value, sort_keys=True, default=_default))


def _default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    # Not str(): an object's default repr carries its address, and a contract that
    # changes on every run would fail the check at random.
    raise TypeError(f"{type(value).__name__} in a payload has no stable JSON form; teach _default to write it")


def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    changed = {key: value for key, value in after.items() if before.get(key, ...) != value}
    removed = {key: "<removed>" for key in before if key not in after}
    return dict(sorted({**changed, **removed}.items()))


WIRE_PARTS = {"json": "body", "params": "query", "headers": "headers"}
"""``_call_kwargs`` keys -> where on the request they land, as a contract names them."""


def _payload(provider: Any, method: str, request: BaseModel) -> dict[str, Any]:
    """The request as sent, flattened.

    Through ``_call_kwargs`` where a provider has one, because that is where a
    query string or a header joins the body -- Jina Reader takes its format as a
    header. The other providers send ``_build_payload`` as the body.
    """
    provider.validate(request)
    args = (request, request.urls) if method == "extract" else (request,)  # type: ignore[attr-defined]
    if hasattr(provider, "_call_kwargs"):
        sent = provider._call_kwargs(*args)
        return _flatten(_jsonable({WIRE_PARTS[key]: value for key, value in sent.items()}))
    return _flatten(_jsonable({"body": provider._build_payload(*args)}))


def _probe(provider: Any, method: str, option: str, value: Any) -> dict[str, Any]:
    """What setting ``option`` to ``value`` changes on the wire, or why it is refused."""
    make = METHODS[method].request
    base_fields = PREREQUISITES.get(option, {})
    try:
        before = _payload(provider, method, make(**base_fields))
    except (WebRouterError, ValidationError) as exc:
        raise SystemExit(
            f"{method}/{provider.name}: the base request for {option!r} is refused ({exc}); fix PREREQUISITES"
        ) from exc
    try:
        after = _payload(provider, method, make(**base_fields, **{option: value}))
    except (WebRouterError, ValidationError) as exc:
        return {"refused": type(exc).__name__}
    # No change on the wire: the value is what the provider sends anyway, or the
    # option is applied after the response (Parallel MCP trims max_results itself).
    # Said in words, so it never reads like an option dropped on the way.
    return _diff(before, after) or {"unchanged": "no wire change"}


def _transport(cls: type) -> dict[str, Any]:
    from web_router.providers._http import _HttpProvider
    from web_router.providers._mcp import McpSearchMixin

    if issubclass(cls, McpSearchMixin):
        return {"kind": "mcp", "url": cls.base_url, "tool": cls.mcp_tool}  # type: ignore[attr-defined]
    if issubclass(cls, _HttpProvider):
        paths = {
            name: getattr(cls, name)
            for name in sorted(dir(cls))
            if name.endswith("_path") and isinstance(getattr(cls, name), str)
        }
        paths.update(_endpoint_constants(cls))
        if not paths:
            raise SystemExit(
                f"{cls.__qualname__}: found no endpoint; name it as a *_path attribute or a *_PATH constant"
            )
        transport: dict[str, Any] = {"kind": "http", "base_url": cls.base_url, "paths": dict(sorted(paths.items()))}
        # The class's verb is the one its base sends a search or an extract with;
        # research, browse and browser calls choose theirs per request.
        if "search_path" in paths or "extract_path" in paths:
            transport["method"] = cls.http_method
        transport["auth_header"] = cls.api_key_header if cls.env_api_key else None
        return transport
    module = sys.modules[cls.__module__]
    sdks = sorted(
        {
            value.__module__.split(".")[0]
            for value in vars(module).values()
            if isinstance(value, type) and not value.__module__.startswith(("web_router", "builtins", "typing"))
        }
    )
    return {"kind": "sdk", "packages": sdks}


ENDPOINT_SUFFIXES = ("_PATH", "_URL")


def _endpoint_constants(cls: type) -> dict[str, str]:
    """Module constants the provider's own methods use as endpoints, e.g. ``PARALLEL_TASK_RUN_PATH``.

    Read from the methods' bytecode, so an endpoint picked inside ``_start`` or
    ``_fetch`` is recorded as surely as a class attribute is.
    """
    module = sys.modules[cls.__module__]
    found: dict[str, str] = {}

    def walk(code: types.CodeType) -> None:
        for name in code.co_names:
            value = getattr(module, name, None)
            if name.endswith(ENDPOINT_SUFFIXES) and isinstance(value, str):
                found[name.lower()] = value
        for const in code.co_consts:
            if isinstance(const, types.CodeType):
                walk(const)

    for klass in cls.__mro__:
        if klass.__module__ != cls.__module__:
            continue
        for member in vars(klass).values():
            function = getattr(member, "__func__", member)
            if isinstance(function, types.FunctionType):
                walk(function.__code__)
    return found


def _keyless(name: str, method: str) -> str | None:
    """How this provider runs without a credential, if it can."""
    spec = METHODS[method]
    if not spec.env(name):
        return "always"
    for member, kwargs in spec.keyless:
        if member == name:
            return "through auto, " + ", ".join(f"{key}={value!r}" for key, value in kwargs.items())
    return None


def generate(method: str, name: str) -> dict[str, Any]:
    """The generated half of one contract."""
    spec = METHODS[method]
    cls = spec.get_class(name)
    model = REQUEST_MODELS[method]
    provider = cls(api_key=PROBE_KEY)
    try:
        return _contract(spec, cls, model, provider, method, name)
    finally:
        provider.close()


def _contract(
    spec: _Method, cls: type[Any], model: type[BaseModel], provider: Any, method: str, name: str
) -> dict[str, Any]:
    supported: frozenset[str] = cls.supported_parameters
    contract: dict[str, Any] = {
        "provider": name,
        "method": method,
        "class": f"{cls.__module__}.{cls.__qualname__}",
        "transport": _transport(cls),
        "credentials": list(spec.env(name)),
        "keyless": _keyless(name, method),
        "always_sent": dict(sorted(_payload(provider, method, spec.request()).items())),
        "options": {
            option: {_label(option, value): _probe(provider, method, option, value) for value in _values(model, option)}
            for option in _options(model)
            if option in supported
        },
        "not_mapped": sorted(option for option in _options(model) if option not in supported),
    }
    if method == "extract":
        contract["default_format"] = cls.default_format
        contract["batches_urls"] = cls.batches_urls
    return contract


def contract_path(method: str, name: str) -> Path:
    return CONTRACTS_DIR / method / f"{name}.json"


def _hand_fields(path: Path, options: list[str]) -> dict[str, Any]:
    """The fields a person wrote, kept from the committed file, with a ``TODO`` for anything new."""
    existing = json.loads(path.read_text()) if path.is_file() else {}
    verified = existing.get("verified", {})
    return {
        "source": existing.get("source", TODO),
        "verified": {option: verified.get(option, TODO) for option in options},
        "vendor_options": existing.get("vendor_options", TODO),
    }


def render(method: str, name: str) -> str:
    contract = generate(method, name)
    contract.update(_hand_fields(contract_path(method, name), list(contract["options"])))
    text = json.dumps(contract, indent=2, ensure_ascii=False) + "\n"
    if PROBE_KEY in text:
        raise SystemExit(f"{method}/{name}: the probe credential reached the payload; it must not be written out")
    return text


def providers() -> Iterator[tuple[str, str]]:
    """Every built-in (method, provider) that talks to a vendor.

    Built-in only: a provider a caller registers at runtime has no contract here.
    ``auto`` and ``local`` build no payload.
    """
    for method, spec in METHODS.items():
        for name in spec.names():
            if name in spec.builtin and hasattr(spec.get_class(name), "_build_payload"):
                yield method, name


def expected() -> dict[Path, str]:
    """Every contract the code implies, keyed by where it is committed.

    The probe settings replace whatever the environment holds for the duration,
    so the output never depends on whose machine ran it -- and a real Bright Data
    zone, which is sent in the body, never reaches a committed file.
    """
    saved = {key: os.environ.get(key) for key in PROBE_ENV}
    os.environ.update(PROBE_ENV)
    try:
        return {contract_path(method, name): render(method, name) for method, name in providers()}
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def stale(files: dict[Path, str]) -> list[str]:
    """Each committed contract that differs from the code, and each that no provider backs any more."""
    problems = [
        f"{path.relative_to(ROOT)} is out of date"
        for path, text in files.items()
        if not path.is_file() or path.read_text() != text
    ]
    problems += [
        f"{path.relative_to(ROOT)} has no provider behind it"
        for path in sorted(CONTRACTS_DIR.glob("*/*.json"))
        if path not in files
    ]
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit 1 if contracts/ differs from the code")
    args = parser.parse_args()

    files = expected()
    if args.check:
        problems = stale(files)
        for problem in problems:
            print(problem, file=sys.stderr)
        if problems:
            print("Run `make contracts` and commit the result.", file=sys.stderr)
        raise SystemExit(1 if problems else 0)

    for path in sorted(CONTRACTS_DIR.glob("*/*.json")):
        if path not in files:
            path.unlink()
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    print(f"Wrote {len(files)} contracts to {CONTRACTS_DIR.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
