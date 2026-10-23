#!/usr/bin/env python3
"""Evaluate browser-baseline overlap, cross-round domains, and query-term coverage."""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

VERSION = "0.3.0"
REVIEW_FIELDS = [
    "result_id", "capture_file", "query", "round", "result_position", "title",
    "snippet", "displayed_url", "identified_domain", "query_term_coverage",
    "matched_terms", "missing_terms", "manual_relevance_label", "review_reason",
    "reviewer", "reviewed_at",
]
LABELS = {"relevant", "irrelevant", "unclear"}
COMPARISON_SETTINGS = ("language", "country", "proxy_country", "result_count", "route")


def normalize_domain(value):
    if not isinstance(value, str):
        return None
    candidate = value.strip().split("›", 1)[0].strip()
    if not candidate or candidate.startswith("/"):
        return None
    if "://" not in candidate:
        candidate = "https://" + candidate
    try:
        host = (urlparse(candidate).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return None
    if not re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,63}", host):
        return None
    if host == "google.com" or host.endswith(".google.com"):
        return None
    return host


def result_domain(item):
    return normalize_domain(item.get("displayed_url"))


def observed_domains(parsed):
    """Compatibility helper; domain identification uses displayed_url only."""
    items = parsed.get("organic_results", [])
    if not isinstance(items, list):
        return set()
    return {d for item in items if isinstance(item, dict)
            if (d := result_domain(item)) is not None}


def text(value):
    return value if isinstance(value, str) else ""


def tokens(value):
    return set(re.findall(r"[^\W_]+", text(value).casefold(), re.UNICODE))


def lexical_check(query, title, snippet):
    terms = tokens(query)
    title_matches = terms & tokens(title)
    snippet_matches = terms & tokens(snippet)
    matched = title_matches | snippet_matches
    return {
        "query_terms": sorted(terms),
        "title_matched_terms": sorted(title_matches),
        "snippet_matched_terms": sorted(snippet_matches),
        "matched_terms": sorted(matched),
        "missing_terms": sorted(terms - matched),
        "query_term_coverage": len(matched) / len(terms) if terms else None,
    }


def load_object(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def evaluate_capture(record, baseline, filename, capture_digest):
    settings = record.get("settings")
    settings = settings if isinstance(settings, dict) else {}
    query = record.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError(f"Missing or invalid query in {filename}")
    raw = record.get("html_parsed_organic_results")
    has_list = isinstance(raw, list)
    http_ok = record.get("http_status") == 200
    report = {
        "capture_file": filename, "capture_sha256": capture_digest,
        "query": query, "round": settings.get("round"),
        "collected_at": record.get("collected_at"), "settings": settings,
        "http_status": record.get("http_status"),
        "results_source": "html_parsed_organic_results",
        "domain_source": "displayed_url",
        "request_status": "http_200" if http_ok else "request_failed",
        "parser_status": "available" if has_list else "html_parsing_output_missing",
        "baseline_status": "available" if baseline else "no_baseline",
        "requested_result_count": settings.get("result_count"),
        "html_parsed_result_count": len(raw) if has_list else None,
        "observed_domains": [], "results_with_unknown_domain": [],
        "result_details": [],
    }
    if not http_ok:
        report["request_reason"] = record.get("error") or "HTTP status was not 200"
    observed, details, unknown = set(), [], []
    if has_list:
        for position, original in enumerate(raw, 1):
            valid = isinstance(original, dict)
            item = original if valid else {}
            domain = result_domain(item)
            if domain:
                observed.add(domain)
            result_id = hashlib.sha256(
                f"{filename}\0{capture_digest}\0{position}".encode("utf-8")
            ).hexdigest()
            detail = {
                "result_id": result_id, "result_position": position,
                "valid_entry": valid, "title": text(item.get("title")),
                "snippet": text(item.get("snippet")),
                "displayed_url": item.get("displayed_url"),
                "identified_domain": domain,
                **lexical_check(query, item.get("title"), item.get("snippet")),
            }
            details.append(detail)
            if not domain:
                unknown.append({
                    "result_id": result_id, "result_position": position,
                    "title": detail["title"], "displayed_url": item.get("displayed_url"),
                    "reason": "invalid result entry" if not valid else "no usable displayed domain",
                })
    report.update({"observed_domains": sorted(observed),
                   "observed_domain_count": len(observed),
                   "results_with_unknown_domain": unknown, "result_details": details})
    requested = settings.get("result_count")
    if has_list and type(requested) is int:
        delta = len(raw) - requested
        report["extracted_minus_requested"] = delta
        report["count_comparison"] = "equal" if delta == 0 else "below" if delta < 0 else "above"
    else:
        report["count_comparison"] = "unavailable"
    if not http_ok:
        status = "request_failed"
    elif not has_list:
        status = "html_parsing_output_missing"
    elif not raw:
        status = "no_results_extracted"
    elif not observed:
        status = "no_usable_domains"
    else:
        status = "usable_domains_extracted"
    report["evaluation_status"] = status
    eligible = http_ok and has_list
    report["baseline_comparison_status"] = (
        "no_baseline" if not baseline else "capture_not_eligible" if not eligible else "completed"
    )
    report["baseline_domain_count"] = len(baseline)
    if baseline and eligible:
        matched = baseline & observed
        report.update({
            "matched_domain_count": len(matched), "matched_domains": sorted(matched),
            "baseline_domains_not_observed": sorted(baseline - observed),
            "additional_domains": sorted(observed - baseline),
            "baseline_domain_overlap": len(matched) / len(baseline),
        })
    else:
        report["baseline_domain_overlap"] = None
    return report


def compare_rounds(reports, selected_rounds):
    groups = defaultdict(list)
    for report in reports:
        groups[report["query"]].append(report)
    comparisons = []
    for query, captures in sorted(groups.items()):
        by_round = defaultdict(list)
        for capture in captures:
            by_round[capture["round"]].append(capture)
        problems = []
        for round_id in selected_rounds:
            count = len(by_round[round_id])
            if count != 1:
                problems.append(f"{round_id}: expected one capture, found {count}")
        known_values, missing = {}, {}
        for key in COMPARISON_SETTINGS:
            vals = {json.dumps(c["settings"].get(key), sort_keys=True)
                    for c in captures if c["settings"].get(key) is not None}
            known_values[key] = [json.loads(v) for v in sorted(vals)]
            if len(vals) > 1:
                problems.append(f"mismatched setting: {key}")
            missing[key] = [c["capture_file"] for c in captures
                            if c["settings"].get(key) is None]
        for c in captures:
            if c["request_status"] != "http_200" or c["parser_status"] != "available":
                problems.append(f"capture not eligible: {c['capture_file']}")
        summary = {
            "query": query, "selected_rounds": selected_rounds,
            "settings_values": known_values,
            "settings_missing_in_captures": {k: v for k, v in missing.items() if v},
            "round_summaries": [{
                "capture_file": c["capture_file"], "round": c["round"],
                "extracted_entry_count": c["html_parsed_result_count"],
                "identified_domain_count": c["observed_domain_count"],
                "unknown_domain_entry_count": len(c["results_with_unknown_domain"]),
                "baseline_domain_overlap": c["baseline_domain_overlap"],
            } for c in captures],
        }
        if problems:
            summary.update({"comparison_status": "not_compared", "reasons": problems})
            comparisons.append(summary)
            continue
        ordered = [by_round[r][0] for r in selected_rounds]
        sets = [set(c["observed_domains"]) for c in ordered]
        union = set().union(*sets)
        intersection = set.intersection(*sets)
        pairs = []
        for (left, a), (right, b) in itertools.combinations(zip(ordered, sets), 2):
            pairs.append({
                "round_a": left["round"], "round_b": right["round"],
                "shared_domains": sorted(a & b),
                "domains_only_identified_in_a": sorted(a - b),
                "domains_only_identified_in_b": sorted(b - a),
                "domain_jaccard_overlap": len(a & b) / len(a | b) if a | b else None,
            })
        summary.update({
            "comparison_status": "completed" if len(ordered) > 1 else "single_round_only",
            "domains_identified_in_all_rounds": sorted(intersection),
            "domain_presence": {d: {r: d in s for r, s in zip(selected_rounds, sets)}
                                for d in sorted(union)},
            "pairwise_comparisons": pairs,
        })
        comparisons.append(summary)
    return comparisons


def review_rows(reports):
    rows = []
    for capture in reports:
        for item in capture["result_details"]:
            rows.append({
                "result_id": item["result_id"], "capture_file": capture["capture_file"],
                "query": capture["query"], "round": capture["round"],
                "result_position": item["result_position"], "title": item["title"],
                "snippet": item["snippet"], "displayed_url": item["displayed_url"],
                "identified_domain": item["identified_domain"],
                "query_term_coverage": item["query_term_coverage"],
                "matched_terms": json.dumps(item["matched_terms"], ensure_ascii=False),
                "missing_terms": json.dumps(item["missing_terms"], ensure_ascii=False),
                "manual_relevance_label": "", "review_reason": "", "reviewer": "", "reviewed_at": "",
            })
    return rows


def summarize_reviews(rows, review_path):
    supplied = {}
    if review_path:
        with review_path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not {"result_id", "manual_relevance_label"}.issubset(reader.fieldnames or []):
                raise ValueError("Review CSV needs result_id and manual_relevance_label columns")
            for entry in reader:
                key = entry["result_id"]
                if key in supplied:
                    raise ValueError(f"Duplicate review result_id: {key}")
                label = entry["manual_relevance_label"].strip().lower()
                if label and label not in LABELS:
                    raise ValueError(f"Invalid relevance label: {label}")
                supplied[key] = {**entry, "manual_relevance_label": label}
    totals = {k: 0 for k in ("relevant", "irrelevant", "unclear", "unreviewed")}
    grouped = defaultdict(lambda: dict.fromkeys(totals, 0))
    for row in rows:
        entry = supplied.get(row["result_id"], {})
        for field in ("manual_relevance_label", "review_reason", "reviewer", "reviewed_at"):
            row[field] = entry.get(field, "")
        label = row["manual_relevance_label"] or "unreviewed"
        totals[label] += 1
        grouped[(row["query"], row["round"])][label] += 1
    count = len(rows)
    return {
        "totals": totals, "result_appearances": count,
        "review_coverage": (count - totals["unreviewed"]) / count if count else None,
        "by_query_and_round": [{"query": q, "round": r, **counts}
                               for (q, r), counts in sorted(grouped.items())],
        "unmatched_review_ids": sorted(set(supplied) - {r["result_id"] for r in rows}),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/raw"))
    parser.add_argument("--baseline", type=Path, required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--round", dest="round_id")
    selection.add_argument("--rounds", nargs="+")
    parser.add_argument("--reviews", type=Path, help="Previously completed review CSV")
    parser.add_argument("--output", type=Path, default=Path("data/evaluations"))
    args = parser.parse_args()
    rounds = args.rounds or [args.round_id]
    if len(set(rounds)) != len(rounds):
        parser.error("Round labels must be unique")
    if any(not re.fullmatch(r"[A-Za-z0-9_-]+", r) for r in rounds):
        parser.error("Round labels may contain only letters, numbers, _ or -")
    if not args.input.is_dir():
        parser.error(f"Capture directory does not exist: {args.input}")
    try:
        baseline_data = load_object(args.baseline)
        for query, values in baseline_data.items():
            if not isinstance(values, list):
                raise ValueError(f"Baseline for {query!r} must be a list")
        reports = []
        for path in sorted(args.input.glob("*.json")):
            record = load_object(path)
            settings = record.get("settings")
            if not isinstance(settings, dict) or settings.get("round") not in rounds:
                continue
            query = record.get("query")
            values = baseline_data.get(query, []) if isinstance(query, str) else []
            baseline = {d for value in values if (d := normalize_domain(value))}
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            reports.append(evaluate_capture(record, baseline, path.name, digest))
        if not reports:
            parser.error("No captures found for the selected rounds; no outputs saved")
        rows = review_rows(reports)
        review_summary = summarize_reviews(rows, args.reviews)
    except (OSError, ValueError, csv.Error) as exc:
        parser.error(str(exc))
    now = datetime.now(timezone.utc)
    group = rounds[0] if len(rounds) == 1 else "multi-round"
    output_dir = args.output / group
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    review_output = output_dir / f"relevance-review-{stamp}.csv"
    report_path = output_dir / f"evaluation-{stamp}.json"
    saved = {
        "evaluated_at": now.isoformat(), "evaluator_version": VERSION,
        "selected_rounds": rounds, "baseline_file": str(args.baseline),
        "baseline_snapshot": baseline_data, "capture_directory": str(args.input),
        "capture_count": len(reports), "evaluations": reports,
        "cross_round_comparisons": compare_rounds(reports, rounds),
        "manual_relevance_summary": review_summary,
        "review_input_file": str(args.reviews) if args.reviews else None,
        "review_output_file": str(review_output),
    }
    with review_output.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    report_path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Evaluation report saved to: {report_path}")
    print(f"Review CSV saved to: {review_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
