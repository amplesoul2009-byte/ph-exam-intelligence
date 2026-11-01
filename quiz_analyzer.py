"""AI-assisted quiz validation for PH Practice Hub.

Uses Gemini first and Groq as fallback when configured. AI suggestions are
stored for owner review; this module never overwrites original questions or
approves/publishes content.
"""
from __future__ import annotations

import json
import ast
import operator
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)
VALID = set("ABCD")


def _setting(settings: Any, name: str, env_name: str, default: str = "") -> str:
    value = getattr(settings, name, None) if settings is not None else None
    return str(value or os.getenv(env_name, default) or "").strip()


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("AI response did not contain a JSON object.")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("AI response JSON was not an object.")
    return data


def _request_json(url: str, headers: dict, payload: dict, timeout: int = 45) -> str:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return raw


def _call_gemini(api_key: str, model: str, prompt: str) -> str:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
    }
    raw = _request_json(url, {"Content-Type": "application/json"}, payload)
    data = json.loads(raw)
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_groq(api_key: str, model: str, prompt: str) -> str:
    payload = {
        "model": model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "You are a careful exam-question auditor. Return valid JSON only."},
            {"role": "user", "content": prompt},
        ],
    }
    raw = _request_json(
        "https://api.groq.com/openai/v1/chat/completions",
        {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        payload,
    )
    data = json.loads(raw)
    return data["choices"][0]["message"]["content"]


def _ask_ai(parsed, settings) -> tuple[str, dict | None, str | None]:
    gemini_key = _setting(settings, "gemini_api_key", "GEMINI_API_KEY")
    gemini_model = _setting(settings, "gemini_model", "GEMINI_MODEL", "gemini-2.5-flash")
    groq_key = _setting(settings, "groq_api_key", "GROQ_API_KEY")
    groq_model = _setting(settings, "groq_model", "GROQ_MODEL", "llama-3.3-70b-versatile")

    if not gemini_key and not groq_key:
        return "not_configured", None, "No Gemini or Groq API key is configured."

    questions = []
    for i, q in enumerate(parsed.questions, 1):
        questions.append({
            "index": i,
            "source_number": q.number,
            "question": q.question,
            "options": {k: q.options.get(k, "") for k in "ABCD"},
            "provided_answer": q.correct_option,
            "provided_explanation": q.explanation,
        })

    all_results = []
    providers_used = []
    errors = []
    # Keep requests small enough for large imports and avoid one bad chunk
    # preventing the rest of the batch from receiving AI analysis.
    for start in range(0, len(questions), 10):
        chunk = questions[start:start + 10]
        prompt = (
            "Audit these multiple-choice exam questions for factual correctness, "
            "answer-key correctness, ambiguity, option quality, grammar and explanation. "
            "Do not assume the supplied answer is correct. For arithmetic, independently "
            "calculate where possible. Do not invent facts when uncertain. "
            "Return JSON exactly as {\"results\":[{\"index\":1,\"verdict\":\"valid|incorrect|ambiguous|uncertain\","
            "\"confidence\":0.0,\"reason\":\"short reason\",\"correct_answer\":\"A|B|C|D|null\","
            "\"corrected_question\":\"\",\"corrected_options\":{\"A\":\"\",\"B\":\"\",\"C\":\"\",\"D\":\"\"},"
            "\"corrected_explanation\":\"\",\"needs_source_check\":false}]}. "
            "Use the exact global index supplied for each question. Only populate corrected "
            "fields when a concrete correction is justified. If the original cannot be repaired "
            "confidently, mark uncertain. Do not fabricate citations or current-affairs facts. "
            "Questions:\n" + json.dumps(chunk, ensure_ascii=False)
        )

        chunk_data = None
        if gemini_key:
            try:
                content = _call_gemini(gemini_key, gemini_model, prompt)
                chunk_data = _extract_json(content)
                providers_used.append("gemini")
            except Exception as exc:
                logger.warning("Gemini quiz analysis failed for chunk %s: %s", start // 10 + 1, exc)
                errors.append(f"Gemini chunk {start // 10 + 1}: {str(exc)[:180]}")
        if chunk_data is None and groq_key:
            try:
                content = _call_groq(groq_key, groq_model, prompt)
                chunk_data = _extract_json(content)
                providers_used.append("groq")
            except Exception as exc:
                logger.warning("Groq quiz analysis failed for chunk %s: %s", start // 10 + 1, exc)
                errors.append(f"Groq chunk {start // 10 + 1}: {str(exc)[:180]}")

        if chunk_data is None:
            continue
        rows = chunk_data.get("results", [])
        if isinstance(rows, list):
            all_results.extend(row for row in rows if isinstance(row, dict))

    if not all_results:
        return "failed", None, "; ".join(errors) or "AI analysis failed."
    provider_label = "+".join(dict.fromkeys(providers_used)) or "unknown"
    return provider_label, {"results": all_results}, "; ".join(errors) or None


_ALLOWED_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Pow: operator.pow, ast.Mod: operator.mod,
}
_ALLOWED_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _safe_arithmetic(node):
    """Evaluate a tiny arithmetic AST without eval or arbitrary code."""
    if isinstance(node, ast.Expression):
        return _safe_arithmetic(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        left, right = _safe_arithmetic(node.left), _safe_arithmetic(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 8:
            raise ValueError("Exponent outside safe range.")
        value = _ALLOWED_BINOPS[type(node.op)](left, right)
        if abs(value) > 10**12:
            raise ValueError("Result outside safe range.")
        return value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
        return _ALLOWED_UNARY[type(node.op)](_safe_arithmetic(node.operand))
    raise ValueError("Unsupported arithmetic expression.")


def _python_arithmetic_check(question: str, options: dict, answer: str) -> dict | None:
    """Check simple explicit arithmetic questions against numeric options."""
    text = (question or "").replace("×", "*").replace("÷", "/")
    candidates = re.findall(
        r"(?<![A-Za-z])(?:\d+(?:\.\d+)?\s*(?:[+*/%\-]\s*)?)+",
        text,
    )
    for candidate in candidates:
        candidate = candidate.strip()
        if not re.search(r"[+*/%\-]", candidate):
            continue
        try:
            tree = ast.parse(candidate, mode="eval")
            value = float(_safe_arithmetic(tree))
        except Exception:
            continue
        matches = []
        for label in "ABCD":
            raw = str(options.get(label, "")).replace(",", "").strip()
            raw = re.sub(r"^(?:₹|Rs\.?\s*)", "", raw, flags=re.I)
            raw = raw.rstrip("%").strip()
            try:
                option_value = float(raw)
            except ValueError:
                continue
            if abs(option_value - value) <= max(1e-9, abs(value) * 1e-9):
                matches.append(label)
        if len(matches) == 1:
            correct = matches[0]
            if answer != correct:
                return {
                    "correct_answer": correct,
                    "reason": (
                        f"Python independently evaluated {candidate} = {value:g}; "
                        f"option {correct} matches the result."
                    ),
                    "expression": candidate,
                    "computed_value": value,
                }
        return None
    return None


def analyze_import(parsed, settings=None) -> dict:
    """Run structural and optional AI checks; never approve or mutate source."""
    questions = []
    structural_warnings = []
    for index, item in enumerate(parsed.questions, 1):
        options = getattr(item, "options", {}) or {}
        answer = (getattr(item, "correct_option", None) or "").upper()
        warnings = list(getattr(item, "warnings", []) or [])
        valid_structure = (
            bool((getattr(item, "question", "") or "").strip())
            and all((options.get(k) or "").strip() for k in "ABCD")
            and answer in VALID
        )
        if not valid_structure:
            structural_warnings.append(f"Question {item.number} has structural issues.")
        python_check = (
            _python_arithmetic_check(getattr(item, "question", ""), options, answer)
            if answer in VALID else None
        )
        correction = (
            {"correct_answer": python_check["correct_answer"]}
            if python_check else None
        )
        questions.append({
            "index": index,
            "question_number": item.number,
            "review_status": (
                "correction_suggested" if correction else
                "pending_owner_approval" if valid_structure else "needs_review"
            ),
            "structural_valid": valid_structure,
            "answer_key_present": answer in VALID,
            "warnings": warnings,
            "ai_verdict": "not_checked",
            "ai_confidence": None,
            "ai_reason": "",
            "python_check": python_check,
            "suggested_correction": correction,
        })

    provider, ai_data, ai_error = _ask_ai(parsed, settings)
    ai_results = {}
    if ai_data is not None:
        rows = ai_data.get("results", [])
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    try:
                        ai_results[int(row.get("index"))] = row
                    except (TypeError, ValueError):
                        continue

    for record in questions:
        row = ai_results.get(record["index"])
        if row is None:
            record["ai_verdict"] = "not_returned" if ai_data is not None else provider
            record["review_status"] = (
                "correction_suggested" if record.get("suggested_correction")
                else "pending_owner_approval"
                if record["structural_valid"] and record["answer_key_present"]
                else "needs_review"
            )
            continue
        verdict = str(row.get("verdict", "uncertain")).lower()
        if verdict not in {"valid", "incorrect", "ambiguous", "uncertain"}:
            verdict = "uncertain"
        record["ai_verdict"] = verdict
        try:
            confidence = float(row.get("confidence", 0))
            record["ai_confidence"] = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            record["ai_confidence"] = 0.0
        record["ai_reason"] = str(row.get("reason", ""))[:1000]
        corrected_answer = row.get("correct_answer")
        corrected_options = row.get("corrected_options")
        correction = dict(record.get("suggested_correction") or {})
        if corrected_answer in VALID:
            correction["correct_answer"] = corrected_answer
        if isinstance(row.get("corrected_question"), str) and row["corrected_question"].strip():
            correction["question"] = row["corrected_question"].strip()
        if isinstance(corrected_options, dict) and all(
            isinstance(corrected_options.get(k), str) and corrected_options[k].strip()
            for k in "ABCD"
        ):
            correction["options"] = {k: corrected_options[k].strip() for k in "ABCD"}
        if isinstance(row.get("corrected_explanation"), str) and row["corrected_explanation"].strip():
            correction["explanation"] = row["corrected_explanation"].strip()
        if correction:
            record["suggested_correction"] = correction
        record["needs_source_check"] = bool(row.get("needs_source_check", False))
        python_flag = bool(record.get("python_check"))
        record["review_status"] = (
            "correction_suggested" if correction or python_flag
            else "pending_owner_approval"
            if verdict == "valid" and record["structural_valid"]
            else "needs_review"
        )

    complete_ai = ai_data is not None and len(ai_results) == len(questions)
    status = "completed" if complete_ai else "manual_review_required"
    warnings = structural_warnings[:]
    if ai_error:
        warnings.append(ai_error)
    if ai_data is not None and len(ai_results) != len(questions):
        warnings.append("AI returned an incomplete result set; missing questions require manual review.")

    return {
        "ai_status": status,
        "ai_provider_used": provider,
        "message": (
            "AI and structural checks completed. Suggestions are not applied automatically; "
            "owner must inspect and approve every question."
            if complete_ai else
            "Questions were retained. AI validation is incomplete or not configured; "
            "owner review is required before publishing."
        ),
        "questions": questions,
        "warnings": warnings,
        "errors": list(getattr(parsed, "errors", []) or []),
        "ai_error": ai_error,
        "auto_corrections_applied": 0,
    }
