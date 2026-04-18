import json
import re
from dataclasses import dataclass, field


@dataclass
class EvalResult:
    score: float
    found_bug: bool
    matched_indicators: list[str] = field(default_factory=list)
    matched_location: bool = False
    reasoning_excerpt: str = ""
    structured_finding: dict = field(default_factory=dict)


def evaluate_response(raw_output: str, eval_config: dict) -> EvalResult:
    findings = _try_parse_json_findings(raw_output)
    positive = eval_config.get("positive_indicators", [])
    location = eval_config.get("location_indicators", {})
    whole_file_mode = eval_config.get("whole_file_mode", False)

    best_structured_result = _best_structured_result(
        findings, positive, location, whole_file_mode
    )
    if best_structured_result is not None:
        return best_structured_result

    matched = _matches_indicators(raw_output, positive)
    loc_in_text = _text_matches_location(raw_output, location)

    if len(matched) >= 3 and loc_in_text:
        return EvalResult(
            score=1.0,
            found_bug=True,
            matched_indicators=matched,
            matched_location=True,
            reasoning_excerpt=_extract_excerpt(raw_output, matched),
        )
    if len(matched) >= 2:
        return EvalResult(
            score=0.5,
            found_bug=True,
            matched_indicators=matched,
            matched_location=loc_in_text,
            reasoning_excerpt=_extract_excerpt(raw_output, matched),
        )
    if len(matched) >= 1:
        return EvalResult(
            score=0.25,
            found_bug=True,
            matched_indicators=matched,
            matched_location=loc_in_text,
            reasoning_excerpt=_extract_excerpt(raw_output, matched),
        )

    return EvalResult(score=0.0, found_bug=False)


def _best_structured_result(
    findings: list[dict],
    positive: list[str],
    location: dict,
    whole_file_mode: bool,
) -> EvalResult | None:
    best_result: EvalResult | None = None
    for finding in findings:
        result = _score_structured_finding(
            finding, positive, location, whole_file_mode
        )
        if best_result is None or _is_better_result(result, best_result):
            best_result = result

    if best_result and best_result.score > 0:
        return best_result
    return None


def _score_structured_finding(
    finding: dict,
    positive: list[str],
    location: dict,
    whole_file_mode: bool,
) -> EvalResult:
    loc_match = _matches_location(finding, location)
    indicator_matches = _matches_indicators(json.dumps(finding), positive)
    excerpt = _structured_excerpt(finding)

    if whole_file_mode:
        if len(indicator_matches) >= 2:
            return EvalResult(
                score=1.0,
                found_bug=True,
                matched_indicators=indicator_matches,
                matched_location=loc_match,
                reasoning_excerpt=excerpt,
                structured_finding=finding,
            )
        if indicator_matches:
            return EvalResult(
                score=0.5,
                found_bug=True,
                matched_indicators=indicator_matches,
                matched_location=loc_match,
                reasoning_excerpt=excerpt,
                structured_finding=finding,
            )
        return EvalResult(score=0.0, found_bug=False)

    if loc_match and len(indicator_matches) >= 2:
        return EvalResult(
            score=1.0,
            found_bug=True,
            matched_indicators=indicator_matches,
            matched_location=True,
            reasoning_excerpt=excerpt,
            structured_finding=finding,
        )
    if indicator_matches:
        return EvalResult(
            score=0.5,
            found_bug=True,
            matched_indicators=indicator_matches,
            matched_location=loc_match,
            reasoning_excerpt=excerpt,
            structured_finding=finding,
        )
    return EvalResult(score=0.0, found_bug=False)


def _is_better_result(candidate: EvalResult, current: EvalResult) -> bool:
    if candidate.score != current.score:
        return candidate.score > current.score
    if candidate.matched_location != current.matched_location:
        return candidate.matched_location and not current.matched_location
    return len(candidate.matched_indicators) > len(current.matched_indicators)


def _structured_excerpt(finding: dict) -> str:
    description = finding.get("description")
    if isinstance(description, str) and description:
        return description[:500]
    return json.dumps(finding)[:500]


def _try_parse_json_findings(text: str) -> list[dict]:
    for candidate in _iter_json_values(text):
        findings = _normalize_findings(candidate)
        if findings is not None:
            return findings
    return []


def _iter_json_values(text: str):
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        try:
            value, end = decoder.raw_decode(text, idx)
            yield value
            idx = end
        except (json.JSONDecodeError, ValueError):
            idx += 1


def _normalize_findings(data) -> list[dict] | None:
    if isinstance(data, str):
        return _try_parse_json_findings(data)

    if isinstance(data, dict):
        if "result" in data:
            return _normalize_findings(data["result"])
        return [data]

    if isinstance(data, list) and all(isinstance(item, dict) for item in data):
        return data

    return None


def _matches_location(finding: dict, location: dict) -> bool:
    file_pattern = location.get("file_pattern", "")
    line_range = location.get("line_range", [])

    file_match = False
    if file_pattern:
        file_val = finding.get("file", "")
        file_match = bool(re.search(file_pattern, file_val, re.IGNORECASE))

    line_match = False
    if line_range and len(line_range) == 2:
        line_start = finding.get("line_start", 0)
        line_end = finding.get("line_end", line_start)
        low, high = line_range
        line_match = (
            (low <= line_start <= high)
            or (low <= line_end <= high)
            or (line_start <= low and line_end >= high)
        )

    if file_pattern and line_range:
        return file_match and line_match
    return file_match or line_match


def _text_matches_location(text: str, location: dict) -> bool:
    file_pattern = location.get("file_pattern", "")
    if file_pattern and re.search(file_pattern, text, re.IGNORECASE):
        return True
    return False


def _matches_indicators(text: str, indicators: list[str]) -> list[str]:
    matched = []
    for pattern in indicators:
        if re.search(pattern, text, re.IGNORECASE):
            matched.append(pattern)
    return matched


def _extract_excerpt(text: str, matched: list[str]) -> str:
    if not matched:
        return ""
    for pattern in matched:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            start = max(0, m.start() - 200)
            end = min(len(text), m.end() + 200)
            return text[start:end]
    return text[:500]
