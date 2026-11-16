"""Small, shared checks for fixture configuration."""

COREDUMP_FIELDS = {"wst_ptr", "rst_ptr", "pc", "halt_reason", "exit_code", "cycles",
                   "wst", "rst", "wst_top", "rst_top", "ram", "dev"}


def verification_rules(value):
    rules = [value] if isinstance(value, dict) else value
    if not isinstance(rules, list) or not rules:
        raise ValueError("verify must contain at least one assertion")
    fields = {
        "stdout_match": {"expected_stdout", "match"},
        "stderr_match": {"expected_stderr", "match"},
        "error_match": {"expected_stderr", "match"},
        "binary_match": {"expected_rom"}, "hash_match": {"expected_hash"},
        "size_match": {"expected_size"}, "coredump": COREDUMP_FIELDS,
    }
    for spec in rules:
        if not isinstance(spec, dict) or not isinstance(spec.get("type"), str) or spec["type"] not in fields:
            raise ValueError("unknown or missing verification type")
        kind = spec["type"]
        unknown = set(spec) - fields[kind] - {"type"}
        if unknown:
            raise ValueError(f"Unknown {kind} fields: {', '.join(sorted(unknown))}")
        if kind in {"stdout_match", "stderr_match", "error_match"}:
            key = "expected_stdout" if kind == "stdout_match" else "expected_stderr"
            expected = spec.get(key)
            if not isinstance(expected, str):
                raise ValueError(f"{key} must be a string")
            if spec.get("match", "contains") not in ("exact", "contains"):
                raise ValueError("match must be exact or contains")
            if not expected and kind != "error_match" and spec.get("match") != "exact":
                raise ValueError("empty output assertions require match: exact")
        elif kind == "coredump":
            if not set(spec) - {"type"}:
                raise ValueError("coredump requires state assertions")
            for field, expected in spec.items():
                if field == "type":
                    continue
                if field in {"wst", "rst"} and isinstance(expected, list):
                    if not expected or len(expected) > 256 or any(type(v) is not int or not 0 <= v <= 255 for v in expected):
                        raise ValueError(f"{field} must contain 1..256 bytes; use a pointer assertion for an empty stack")
                elif field in {"wst", "rst", "ram", "dev"}:
                    if not isinstance(expected, dict) or not expected:
                        raise ValueError(f"{field} must contain address assertions")
                    for address, values in expected.items():
                        limit = 65536 if field == "ram" else 256
                        try:
                            parts = address.split("..")
                            start = int(parts[0], 0)
                            end = int(parts[1], 0) if len(parts) == 2 else start + len(values) - 1
                            valid = (len(parts) <= 2 and 0 <= start <= end < limit
                                     and isinstance(values, list) and len(values) == end - start + 1
                                     and all(type(v) is int and 0 <= v <= 255 for v in values))
                        except (ValueError, TypeError, AttributeError):
                            valid = False
                        if not valid:
                            raise ValueError(f"invalid {field} address assertion: {address}")
                elif type(expected) is not int or expected < 0:
                    raise ValueError(f"{field} must be a nonnegative integer")
        elif kind == "size_match":
            if type(spec.get("expected_size")) is not int or spec["expected_size"] < 0:
                raise ValueError("expected_size must be a nonnegative integer")
        else:
            key = "expected_rom" if kind == "binary_match" else "expected_hash"
            if not isinstance(spec.get(key), str) or not spec[key]:
                raise ValueError(f"{key} must be a nonempty string")
    return rules


def validate_fixture(config):
    if not isinstance(config, dict):
        raise ValueError("fixture must be a JSON object")
    if config.get("type", "assembler") not in ("assembler", "interpreter"):
        raise ValueError("type must be assembler or interpreter")
    run = config.get("run")
    if not isinstance(run, dict) or not isinstance(run.get("input"), str) or not run["input"]:
        raise ValueError("run.input must name a source file")
    if "stdin" in run and not isinstance(run["stdin"], str):
        raise ValueError("run.stdin must be a string")
    timeout = run.get("timeout", 1)
    if type(timeout) not in {int, float} or not 0 < timeout < float("inf"):
        raise ValueError("run.timeout must be positive and finite")
    caps = config.get("capabilities", [])
    if not isinstance(caps, list) or any(not isinstance(cap, str) for cap in caps):
        raise ValueError("capabilities must be a list of strings")
    verification_rules(config.get("verify"))
