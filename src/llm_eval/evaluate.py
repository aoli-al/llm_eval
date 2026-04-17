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
    finding = _try_parse_json_finding(raw_output)
    print(f"Parsed finding: {finding}")
    positive = eval_config.get("positive_indicators", [])
    location = eval_config.get("location_indicators", {})

    if finding:
        loc_match = _matches_location(finding, location)
        indicator_matches = _matches_indicators(
            json.dumps(finding), positive
        )
        if loc_match and len(indicator_matches) >= 2:
            return EvalResult(
                score=1.0,
                found_bug=True,
                matched_indicators=indicator_matches,
                matched_location=True,
                reasoning_excerpt=finding.get("description", "")[:500],
                structured_finding=finding,
            )
        if indicator_matches:
            return EvalResult(
                score=0.5,
                found_bug=True,
                matched_indicators=indicator_matches,
                matched_location=loc_match,
                reasoning_excerpt=finding.get("description", "")[:500],
                structured_finding=finding,
            )

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


def _try_parse_json_finding(text: str) -> dict | None:
    print(f"Trying to parse JSON from output: {text[:200]}...")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            if "result" in data:
                result = data["result"]
                if isinstance(result, str):
                    return _try_parse_json_finding(result)
                if isinstance(result, dict):
                    return result
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    for match in re.finditer(r'\{[\s\S]*?\}', text):
        try:
            obj = json.loads(match.group())
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            continue

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
