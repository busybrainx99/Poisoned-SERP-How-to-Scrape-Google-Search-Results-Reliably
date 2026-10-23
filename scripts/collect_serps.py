#!/usr/bin/env python3
"""Collect Google SERPs through the Zenrows Fetch API."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import platform
from datetime import datetime, timezone
from pathlib import Path
import time
from urllib.parse import urlencode, urljoin

# new
from bs4 import BeautifulSoup 

import requests
from dotenv import load_dotenv
from requests import __version__ as requests_version

FETCH_ENDPOINT = "https://api.zenrows.com/v1/"
SAFE_HEADERS = ("X-Request-Id", "X-Request-Cost", "X-Request-Credits")


def redact_secret(value: str, secret: str) -> str:
    """Remove an API key if an HTTP library includes it in an error string."""
    return value.replace(secret, "[REDACTED]") if secret else value


def safe_filename(value: str) -> str:
    """Return a portable filename component for a search query."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:80] or "query"


def capture_filename(query: str, settings: dict[str, str | int], collected_at: str) -> str:
    """Include settings in the name so localized captures cannot overwrite."""
    encoded = json.dumps(settings, sort_keys=True, separators=(",", ":")).encode()
    suffix = hashlib.sha256(encoded).hexdigest()[:10]
    timestamp = collected_at.replace(":", "").replace("-", "")
    return f"{safe_filename(query)}-{suffix}-{timestamp}.json"


def build_google_url(query: str, language: str, country: str, result_count: int) -> str:
    """Build the localized Google URL documented for the SERP workflow."""
    params = {"q": query, "hl": language, "gl": country, "num": result_count}
    return "https://www.google.com/search?" + urlencode(params)


def parse_organic_html(html: str) -> list[dict]:
    """Extract organic web-result entries from the returned HTML."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen = set()

    for block in soup.select("div.tF2Cxc"):
        heading = block.find("h3")
        if heading is None:
            continue

        anchor = heading.find_parent("a")
        if anchor is None:
            continue

        title = heading.get_text(" ", strip=True)
        link = anchor.get("href", "")
        if not title or not link:
            continue

        identity = (title, link)
        if identity in seen:
            continue
        seen.add(identity)

        displayed_url = None
        for cite in block.find_all("cite"):
            text = cite.get_text(" ", strip=True)
            if text.startswith(("https://", "http://")):
                displayed_url = text
                break

        snippet_element = block.select_one(".VwiC3b")

        results.append({
            "title": title,
            "url": link,
            "displayed_url": displayed_url,
            "snippet": (
                snippet_element.get_text(" ", strip=True)
                if snippet_element is not None
                else None
            ),
        })

    return results


def fetch_serp(row: dict[str, str], api_key: str, session: requests.Session) -> dict:
    """Fetch one SERP and return an evidence record suitable for JSON storage."""
    query = row["query"].strip()
    language = row["language"].strip()
    country = row["country"].strip().lower()
    proxy_country = row["proxy_country"].strip().lower()
    result_count = int(row["result_count"])
    if not query:
        raise ValueError("query must not be empty")
    if result_count < 1 or result_count > 100:
        raise ValueError("result_count must be between 1 and 100")

    target_url = build_google_url(query, language, country, result_count)
    params = {
        "apikey": api_key,
        "url": target_url,
        "mode": "auto",
        "proxy_country": proxy_country,
        "extract": "auto",
    }

    started_at = datetime.now(timezone.utc)
    response = session.get(FETCH_ENDPOINT, params=params, timeout=180)
    elapsed_seconds = response.elapsed.total_seconds()
    record = {
        "collected_at": started_at.isoformat(),
        "elapsed_seconds": elapsed_seconds,
        "http_status": response.status_code,
        "query": query,
        "google_url": target_url,
        "settings": {
            "language": language,
            "country": country,
            "proxy_country": proxy_country,
            "result_count": result_count,
            "round": row.get("round", "unspecified").strip(),
            "route": row.get("route", "zenrows_fetch").strip(),
            "mode": "auto",
            "extract": "auto",
        },
        "runtime": {
            "python": platform.python_version(),
            "requests": requests_version,
            "collector": "0.1.0",
        },
        "response_headers": {
            name: response.headers[name]
            for name in SAFE_HEADERS
            if name in response.headers
        },
    }
    if not response.ok:
        record["error"] = redact_secret(response.text[:4000], api_key)
        return record

    body = response.json()
    if not isinstance(body, dict) or not isinstance(body.get("parsed"), dict):
        record["error"] = "Fetch response did not contain a parsed object"
        record["response_excerpt"] = response.text[:4000]
        return record

    # record["parsed"] = body["parsed"]
    # record["html"] = body.get("html")
    # return record

    # new
    # record["parsed"] = body["parsed"]
    # record["html"] = body.get("html")

    # auto_results = record["parsed"].get("organic_results")

    # if isinstance(auto_results, list) and auto_results:
    #     record["organic_results_source"] = "zenrows_auto"
    # else:
    #     html = record["html"]
    #     fallback_results = (
    #         parse_organic_html(html)
    #         if isinstance(html, str) and html.strip()
    #         else []
    #     )

    #     record["html_fallback_organic_results"] = fallback_results
    #     record["organic_results_source"] = (
    #         "html_fallback" if fallback_results else "none"
    #     )

    # return record

    #newsest
    record["parsed"] = body["parsed"]
    record["html"] = body.get("html")

    html = record["html"]
    record["html_parsed_organic_results"] = (
        parse_organic_html(html)
        if isinstance(html, str) and html.strip()
        else []
    )

    return record

def read_queries(path: Path) -> list[dict[str, str]]:
    required = {"query", "language", "country", "proxy_country", "result_count"}
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"CSV must include columns: {', '.join(sorted(required))}")
        return list(reader)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("queries.csv"))
    parser.add_argument("--output", type=Path, default=Path("data/raw"))
    parser.add_argument("--limit", type=int, help="maximum number of CSV rows to collect")
    parser.add_argument("--min-delay-seconds", type=float, default=0.0)
    parser.add_argument(
        "--max-credit-budget",
        type=int,
        help="required for live runs; worst-case estimate uses 25 credits per request",
    )
    parser.add_argument("--dry-run", action="store_true", help="show planned requests without calling Fetch")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.min_delay_seconds < 0:
        parser.error("--min-delay-seconds cannot be negative")

    rows = read_queries(args.input)
    if args.limit is not None:
        rows = rows[: args.limit]
    if args.dry_run:
        for row in rows:
            print(json.dumps({key: value for key, value in row.items()}, sort_keys=True))
        print(f"planned_requests={len(rows)} worst_case_credits={len(rows) * 25}")
        return 0
    if args.max_credit_budget is None or args.max_credit_budget < 1:
        parser.error("live collection requires --max-credit-budget with a positive value")
    worst_case_credits = len(rows) * 25
    if worst_case_credits > args.max_credit_budget:
        parser.error(
            f"planned worst-case cost is {worst_case_credits} credits, above budget "
            f"{args.max_credit_budget}; reduce rows or raise the approved budget"
        )

    load_dotenv()
    api_key = os.getenv("ZENROWS_API_KEY")
    if not api_key:
        parser.error("ZENROWS_API_KEY is missing; set it in .env or the environment")

    args.output.mkdir(parents=True, exist_ok=True)
    failed = 0
    with requests.Session() as session:
        for index, row in enumerate(rows):
            if index and args.min_delay_seconds:
                time.sleep(args.min_delay_seconds)
            query = row["query"].strip()
            try:
                record = fetch_serp(row, api_key, session)
                destination = args.output / capture_filename(
                    query, record["settings"], record["collected_at"]
                )
                destination.write_text(json.dumps(record, indent=2), encoding="utf-8")
                outcome = "saved" if "parsed" in record else "saved failure record"
                print(f"{outcome} {destination}")
                if "parsed" not in record:
                    failed += 1
            except requests.RequestException as error:
                failed += 1
                failure = {
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                    "query": query,
                    "settings": {key: value for key, value in row.items()},
                    "runtime": {
                        "python": platform.python_version(),
                        "requests": requests_version,
                        "collector": "0.1.0",
                    },
                    "error": redact_secret(str(error), api_key),
                }
                destination = args.output / capture_filename(
                    query, row, failure["collected_at"]
                )
                destination.write_text(json.dumps(failure, indent=2), encoding="utf-8")
                print(f"saved transport failure {destination}", file=sys.stderr)
            except (ValueError, KeyError) as error:
                failed += 1
                print(f"failed {query!r}: {error}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
