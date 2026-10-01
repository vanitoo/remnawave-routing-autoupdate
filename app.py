import base64
import copy
import json
import logging
import os
import time

import requests

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("routing-updater")

REMNA_BASE_URL = os.environ["REMNA_BASE_URL"].rstrip("/")
REMNA_API_URL = f"{REMNA_BASE_URL}/subscription-settings"
REMNA_TOKEN = os.environ["REMNA_TOKEN"].strip()

GITHUB_RAW_URL = os.environ.get(
    "GITHUB_RAW_URL",
    "https://raw.githubusercontent.com/pincetgore/PinRouting/refs/heads/main/HAPP/DEFAULT.DEEPLINK",
).strip()
RESPONSE_RULE_NAME = os.environ.get("RESPONSE_RULE_NAME", "Happ Clients").strip()
CHECK_INTERVAL = max(60, int(os.environ.get("CHECK_INTERVAL", "3600")))
REQUEST_TIMEOUT = max(5, int(os.environ.get("REQUEST_TIMEOUT", "30")))

ROUTING_HEADER = "routing"
HAPP_PREFIX = "happ://routing/onadd/"

REMNA_HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {REMNA_TOKEN}",
}
if REMNA_BASE_URL.startswith("http://"):
    # The updater normally talks to Remnawave directly over the Docker network,
    # while the public panel itself is served through HTTPS.
    REMNA_HEADERS["X-Forwarded-Proto"] = "https"
    REMNA_HEADERS["X-Forwarded-For"] = "127.0.0.1"


def request(method: str, url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    response = requests.request(method, url, **kwargs)
    response.raise_for_status()
    return response


def get_remna_settings() -> dict:
    response = request("GET", REMNA_API_URL, headers=REMNA_HEADERS)
    payload = response.json()
    return payload.get("response", payload)


def patch_remna_settings(payload: dict) -> dict:
    response = request(
        "PATCH",
        REMNA_API_URL,
        headers={**REMNA_HEADERS, "Content-Type": "application/json"},
        json=payload,
    )
    result = response.json()
    return result.get("response", result)


def fetch_happ_deeplink() -> tuple[str, dict]:
    response = request("GET", GITHUB_RAW_URL)
    deeplink = response.text.strip()

    if not deeplink.startswith(HAPP_PREFIX):
        raise ValueError(
            f"Unexpected routing payload: expected prefix {HAPP_PREFIX!r}"
        )

    encoded = deeplink[len(HAPP_PREFIX):].strip()
    if not encoded:
        raise ValueError("Happ routing deeplink contains an empty Base64 payload")

    try:
        decoded = base64.b64decode(encoded, validate=True)
        profile = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise ValueError("Happ routing deeplink contains invalid Base64/JSON") from exc

    if not isinstance(profile, dict):
        raise ValueError("Happ routing profile is not a JSON object")

    name = profile.get("Name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Happ routing profile has no valid Name")

    return deeplink, profile


def get_header(headers: list[dict], key: str) -> str:
    for header in headers:
        if str(header.get("key", "")).lower() == key.lower():
            return str(header.get("value", "")).strip()
    return ""


def set_header(headers: list[dict], key: str, value: str) -> bool:
    """Set one response header. Returns True when the list changed."""
    matches = [
        index
        for index, header in enumerate(headers)
        if str(header.get("key", "")).lower() == key.lower()
    ]

    if not matches:
        headers.append({"key": key, "value": value})
        return True

    changed = False
    first = matches[0]
    if headers[first].get("key") != key or headers[first].get("value") != value:
        headers[first] = {"key": key, "value": value}
        changed = True

    # Keep exactly one routing header in the rule.
    for index in reversed(matches[1:]):
        del headers[index]
        changed = True

    return changed


def remove_global_routing(custom_headers: dict | None) -> tuple[dict, bool]:
    headers = dict(custom_headers or {})
    cleaned = {
        key: value
        for key, value in headers.items()
        if key.lower() != ROUTING_HEADER
    }
    return cleaned, cleaned != headers


def get_target_rule(response_rules: dict) -> dict:
    if not isinstance(response_rules, dict):
        raise ValueError("Remnawave responseRules is missing or invalid")

    rules = response_rules.get("rules")
    if not isinstance(rules, list):
        raise ValueError("Remnawave responseRules.rules is missing or invalid")

    target = next(
        (rule for rule in rules if rule.get("name") == RESPONSE_RULE_NAME),
        None,
    )
    if target is None:
        raise ValueError(
            f"Response rule {RESPONSE_RULE_NAME!r} was not found in Remnawave"
        )
    return target


def get_rule_routing(response_rules: dict) -> tuple[str, bool]:
    target = get_target_rule(response_rules)
    modifications = target.get("responseModifications")
    if not isinstance(modifications, dict):
        return "", False

    headers = modifications.get("headers")
    if not isinstance(headers, list):
        return "", modifications.get("applyHeadersToEnd") is True

    return (
        get_header(headers, ROUTING_HEADER),
        modifications.get("applyHeadersToEnd") is True,
    )


def update_response_rules(response_rules: dict, deeplink: str) -> tuple[dict, str, bool]:
    updated = copy.deepcopy(response_rules)
    target = get_target_rule(updated)

    response_type = str(target.get("responseType", ""))
    if response_type != "XRAY_JSON":
        log.warning(
            "Rule %r has responseType=%r (expected XRAY_JSON for Happ)",
            RESPONSE_RULE_NAME,
            response_type,
        )

    modifications = target.setdefault("responseModifications", {})
    if not isinstance(modifications, dict):
        raise ValueError(
            f"Rule {RESPONSE_RULE_NAME!r} has invalid responseModifications"
        )

    headers = modifications.setdefault("headers", [])
    if not isinstance(headers, list):
        raise ValueError(
            f"Rule {RESPONSE_RULE_NAME!r} has invalid responseModifications.headers"
        )

    current = get_header(headers, ROUTING_HEADER)
    changed = set_header(headers, ROUTING_HEADER, deeplink)

    if modifications.get("applyHeadersToEnd") is not True:
        modifications["applyHeadersToEnd"] = True
        changed = True

    return updated, current, changed


def verify_applied(expected_deeplink: str) -> None:
    settings = get_remna_settings()
    current, applied_to_end = get_rule_routing(settings.get("responseRules"))

    if current != expected_deeplink:
        raise RuntimeError("Verification failed: routing header was not stored")
    if not applied_to_end:
        raise RuntimeError("Verification failed: applyHeadersToEnd is not enabled")

    _, has_global = remove_global_routing(settings.get("customResponseHeaders"))
    if has_global:
        raise RuntimeError("Verification failed: global routing header still exists")


def run_cycle() -> None:
    deeplink, profile = fetch_happ_deeplink()
    settings = get_remna_settings()

    settings_uuid = settings.get("uuid")
    if not settings_uuid:
        raise ValueError("Remnawave response has no subscription settings UUID")

    response_rules, current, rules_changed = update_response_rules(
        settings.get("responseRules"),
        deeplink,
    )
    custom_headers, global_changed = remove_global_routing(
        settings.get("customResponseHeaders")
    )

    profile_name = profile.get("Name", "?")
    last_updated = profile.get("LastUpdated", "?")

    if not rules_changed and not global_changed:
        log.info(
            "No changes: %s is already current (LastUpdated=%s)",
            profile_name,
            last_updated,
        )
        return

    payload = {
        "uuid": settings_uuid,
        "responseRules": response_rules,
    }
    if global_changed:
        payload["customResponseHeaders"] = custom_headers
        log.info("Removing legacy global routing header")

    if current:
        log.info(
            "Updating %r routing (%d -> %d chars)",
            RESPONSE_RULE_NAME,
            len(current),
            len(deeplink),
        )
    else:
        log.info(
            "Adding routing header to %r (%d chars)",
            RESPONSE_RULE_NAME,
            len(deeplink),
        )

    patch_remna_settings(payload)
    verify_applied(deeplink)
    log.info(
        "Routing updated successfully: %s (LastUpdated=%s)",
        profile_name,
        last_updated,
    )


def main() -> None:
    if not REMNA_TOKEN:
        raise SystemExit("REMNA_TOKEN is empty")
    if not RESPONSE_RULE_NAME:
        raise SystemExit("RESPONSE_RULE_NAME is empty")

    log.info("Starting Remnawave Happ routing updater")
    log.info("Remnawave API: %s", REMNA_API_URL)
    log.info("Source: %s", GITHUB_RAW_URL)
    log.info("Target response rule: %s", RESPONSE_RULE_NAME)
    log.info("Check interval: %ds", CHECK_INTERVAL)

    while True:
        try:
            run_cycle()
            sleep_for = CHECK_INTERVAL
        except Exception:
            log.exception("Routing update cycle failed")
            sleep_for = min(CHECK_INTERVAL, 300)
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
