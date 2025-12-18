#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import html
import json
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple


# json tag "<", ">" pairs
TAG_REGEX = re.compile(r"<[^>]+>")
# redundant white spaces
WS_REGEX = re.compile(r"\s+")

LIKELY_TITLE_COLS = ["Title", "title", "FormTitle", "formTitle", "Form Name", "Name"]
LIKELY_PAYLOAD_NAME = [
    "Json",
    "json",
    "Payload",
    "payload",
    "Response",
    "response",
    "FormJson",
    "Form JSON",
    "Data",
    "data",
    "Answers",
    "answers",
]
LIKELY_DATE_COLS = [
    "Submittted At",  # what Splose is using has this typo lol
    "SubmittedAt",
    "Submitted At",
    "submittedAt",
    "submitted_at",
    "CreatedAt",
    "Created At",
    "createdAt",
    "created_at",
    "Date",
    "date",
    "Timestamp",
    "timestamp",
]

META_DEFAULT_COLS = [
    # "Id",
    # "ID",
    # "FormId",
    # "FormID",
    # "ClientId",
    # "ClientID",
    "ClientName",
    "Client Name",
    "Patient Name",
    # "CreatedAt",
    # "Created At",
    # "SubmittedAt",
    # "Submitted At",
    # "Status",
    # "Title",
]


def norm_title(s: str) -> str:
    return WS_REGEX.sub(" ", (s or "").strip())


def clean_text(s: Any) -> str:
    if s is None:
        return ""
    s = html.unescape(str(s))
    s = TAG_REGEX.sub(" ", s)
    s = WS_REGEX.sub(" ", s).strip()
    return s


def parse_date(s: Any) -> Optional[dt.date]:
    """
    Parses common date strings found in exports.
    Accepts:
      - YYYY-MM-DD
      - ISO8601 datetime (YYYY-MM-DDTHH:MM:SS...Z)
      - 'YYYY-MM-DD HH:MM:SS'
    Returns date() or None.
    """
    if not isinstance(s, str):
        return None
    t = s.strip()
    if not t:
        return None

    # datetime does NOT recognise such time format
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"

    # take first 10 chars if looks like ISO date prefix
    if len(t) >= 10 and re.match(r"^\d{4}-\d{2}-\d{2}", t):
        try:
            return dt.date.fromisoformat(t[:10])
        except Exception:
            return None

    # try a couple of common formats
    for fmt in "%d/%m/%Y":
        try:
            return dt.datetime.strptime(t, fmt).date()
        except Exception:
            pass

    return None


def parse_json_sections(cell: Any) -> Optional[List[Dict[str, Any]]]:
    """
    Convert json dict/list into List.
    """
    if not isinstance(cell, str):
        return None
    s = cell.strip()
    if not s:
        return None
    if not (s.startswith("[") or s.startswith("{")):
        return None

    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        try:
            obj = json.loads(s.strip('"'))
        except Exception:
            return None
    except Exception:
        return None

    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for k in ("sections", "data", "form", "payload", "answers"):
            v = obj.get(k)
            if isinstance(v, list):
                return v
    return None


def detect_col(fieldnames: List[str], candidates: List[str]) -> Optional[str]:
    """
    find the fieldname of a interested field.
    """
    for c in candidates:
        if c in fieldnames:
            return c
    return None


def detect_json_col(
    fieldnames: List[str], sample_rows: List[Dict[str, str]]
) -> Optional[str]:
    """
    find the key of payload block
    """
    # 1) try likely names
    for name in LIKELY_PAYLOAD_NAME:
        if name in fieldnames:
            for r in sample_rows[:25]:
                if parse_json_sections(r.get(name)):
                    return name

    # 2) brute-force: look for key that has value that consists questions/title
    for name in fieldnames:
        for r in sample_rows[:25]:
            sec = parse_json_sections(r.get(name))
            if (
                sec
                and isinstance(sec, list)
                and isinstance(sec[0], dict)
                and ("questions" in sec[0] or "title" in sec[0])
            ):
                return name
    return None


def extract_checkboxes(q: Dict[str, Any]) -> Optional[str]:
    opts = q.get("checkboxes")
    if not isinstance(opts, list):
        return None

    picked = []
    for o in opts:
        if isinstance(o, dict) and o.get("checked") is True:
            val = (o.get("label") or o.get("value") or "").strip()
            if not val and o.get("customChoice") is True:
                val = "Other"
            if val:
                picked.append(clean_text(val))

    return "; ".join(picked) if picked else None


def extract_answer(
    q: Dict[str, Any], other_interested_keys: List[str]
) -> Optional[str]:
    qtype = norm_title(q.get("type") or "")

    if qtype == "Statement":
        return None

    if qtype in ("Short answer", "Paragraph", "Yes/No"):
        txt = q.get("answerText")
        txt = clean_text(txt) if isinstance(txt, str) else ""
        return txt if txt else None

    if qtype == "Patient name":
        first = clean_text(q.get("firstName") or "")
        last = clean_text(q.get("lastName") or "")
        full = " ".join([x for x in [first, last] if x])
        return full if full else None

    if qtype == "Date of birth":
        y, m, d = q.get("birthYear"), q.get("birthMonth"), q.get("birthDay")
        if isinstance(y, int) and isinstance(m, int) and isinstance(d, int):
            # in splose, month are stored as 0 ~ 11
            return f"{d:02d}/{(m+1):02d}/{y}"
        txt = clean_text(q.get("answerText") or "")
        return txt if txt else None

    if qtype in ("Checkboxes", "Multiple choice"):
        # try return ticked options, otherwise the text
        picked = extract_checkboxes(q)
        if picked:
            return picked
        txt = clean_text(q.get("answerText") or "")
        return txt if txt else None

    if qtype == "Signature":
        sig = q.get("signature")
        return "signed" if (isinstance(sig, str) and sig.strip()) else "not signed"

    txt = []
    for key in ["answerText"] + other_interested_keys:
        if clean_text(q.get(key) or ""):
            txt.append(clean_text(q.get(key) or ""))

    return "; ".join(txt) if txt else None


def unique_title(base: str, q: Dict[str, Any], seen) -> str:
    if base != "" and base not in seen:
        seen.add(base)
        return base

    candidate = base
    # collision: disambiguate deterministically with question id
    qid = (q.get("id") or "").strip()
    if base == "":
        candidate = f"(untitled:{qid})" if qid else "(untitled)"

    final = candidate or base
    k = 2
    while final in seen:
        # add a numeric suffix
        final = f"{candidate} #{k}"
        k += 1

    seen.add(final)
    return final


def collect_question_titles(sections: list[dict]) -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()

    for sec in sections:
        for q in sec.get("questions") or []:
            if norm_title(q.get("type") or "") == "Statement":
                # no response expected for statements
                continue

            base_title = norm_title(q.get("title") or "")
            final_title = unique_title(base_title, q, seen)
            seen.add(final_title)
            titles.append(final_title)

    return titles


def flatten_questions(
    sections: list[dict], other_interested_keys: List[str]
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    seen: set[str] = set()

    for sec in sections:
        for q in sec.get("questions") or []:
            if norm_title(q.get("type") or "") == "Statement":
                # no response expected for statements
                continue

            base_title = norm_title(q.get("title") or "")
            final_title = unique_title(base_title, q, seen)
            seen.add(final_title)

            if q.get("shown") is False:
                continue

            ans = extract_answer(q, other_interested_keys)

            out.setdefault(final_title, [])
            if ans is not None and ans.strip():
                out[final_title].append(ans)

    return out


def read_sample_rows(
    path: str, sample_n: int = 50
) -> tuple[List[str], List[Dict[str, str]]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])

        rows = []
        for i, r in enumerate(reader):
            if i >= sample_n:
                break
            rows.append(r)

        return fieldnames, rows


def iter_rows(path: str) -> Iterable[Dict[str, str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            yield r


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        raise ValueError("Config must be a JSON object.")
    return cfg


def should_keep_row(
    r: Dict[str, str],
    title_col: Optional[str],
    form_titles: Optional[List[str]],
) -> bool:
    # Title filter
    if form_titles is not None:
        if not title_col:
            return False
        if norm_title(r.get(title_col, "")) not in form_titles:
            return False

    return True


def main():
    cfg = load_config("config.json")

    input_csv = cfg.get("input_csv", "splose_export.csv")
    output_csv = cfg.get("output_csv", "splose_flattened.csv")
    if not input_csv:
        raise SystemExit("config.json must include: input_csv")

    # ---- CSV huge-cell fix ----
    try:
        csv.field_size_limit(sys.maxsize)
    except OverflowError:
        csv.field_size_limit(10**9)

    # ---- Filters ----
    form_titles = cfg.get("form_titles", None)
    if isinstance(form_titles, List):
        form_titles = [norm_title(t) for t in form_titles]
    else:
        form_titles = []

    fieldnames, sample = read_sample_rows(input_csv)

    # allow explicit overrides in config
    title_col = cfg.get("title_col") or detect_col(fieldnames, LIKELY_TITLE_COLS)
    json_col = cfg.get("payload_col") or detect_json_col(fieldnames, sample)
    date_col = cfg.get("date_col") or detect_col(fieldnames, LIKELY_DATE_COLS)
    other_interested_keys = cfg.get("other_interested_keys") or []

    if json_col is None:
        raise SystemExit(
            "Could not detect JSON payload column. Set 'payload_col' in configuration."
        )

    # if date_col is None:
    #    print("Warning: could not detect a date column; date filtering will be skipped.", file=sys.stderr)

    meta_cols_cfg = cfg.get("meta_cols")
    if isinstance(meta_cols_cfg, list) and all(
        isinstance(x, str) for x in meta_cols_cfg
    ):
        meta_cols = [c for c in meta_cols_cfg if c in fieldnames]
    else:
        meta_cols = [c for c in META_DEFAULT_COLS if c in fieldnames]
        if title_col and title_col in fieldnames and title_col not in meta_cols:
            meta_cols.append(title_col)
        if date_col and date_col in fieldnames and date_col not in meta_cols:
            meta_cols.append(date_col)

    # PASS 1: collect union of question titles
    union_titles: List[str] = []
    seen = set()

    for r in iter_rows(input_csv):
        if not should_keep_row(r, title_col, form_titles):
            continue

        sections = parse_json_sections(r.get(json_col))
        if not sections:
            continue

        titles = collect_question_titles(sections)
        for t in titles:
            if t not in seen:
                seen.add(t)
                union_titles.append(t)

    # PASS 2: write output
    out_headers = meta_cols + union_titles
    with open(output_csv, "w", encoding="utf-8", newline="") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=out_headers)
        writer.writeheader()

        for r in iter_rows(input_csv):
            if not should_keep_row(r, title_col, form_titles):
                continue

            out_row: Dict[str, str] = {c: (r.get(c, "") or "") for c in meta_cols}

            sections = parse_json_sections(r.get(json_col))
            if not sections:
                for t in union_titles:
                    out_row[t] = "(N/A)"
                writer.writerow(out_row)
                continue

            qmap = flatten_questions(sections, other_interested_keys)

            for t in union_titles:
                if t not in qmap:
                    out_row[t] = "(N/A)"
                else:
                    answers = qmap[t]
                    if not answers:
                        out_row[t] = (
                            "not signed" if t == "Signature" else "(NoResponse)"
                        )
                    else:
                        out_row[t] = "; ".join(answers)

            writer.writerow(out_row)

    print(f"Done. Wrote: {output_csv}")
    print(
        f"Detected columns: title_col={title_col!r}, date_col={date_col!r}, json_col={json_col!r}"
    )


if __name__ == "__main__":
    main()
