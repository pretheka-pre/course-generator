#!/usr/bin/env python3
"""Generate ArchintX-shaped courses using an OpenAI-compatible chat API."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class GenerationError(RuntimeError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def _retry_wait(attempt: int, error: GenerationError) -> None:
    """Wait between transient API failures, honoring Retry-After when supplied."""
    base = error.retry_after if error.retry_after is not None else min(5 * (2 ** attempt), 30)
    delay = max(0.0, base) + random.uniform(0.0, min(2.0, max(0.1, base * 0.1)))
    reason = str(error).splitlines()[0]
    print(f"Retrying in {delay:.1f}s after: {reason}", file=sys.stderr, flush=True)
    time.sleep(delay)


def read_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise GenerationError(f"Cannot read valid JSON from {path}: {exc}") from exc


def load_local_env(path: Path = Path(".env")) -> None:
    """Load simple KEY=VALUE lines without overriding values already in the process."""
    if not path.is_file():
        return
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise GenerationError(f"Invalid .env line {line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] == value[-1:] and value.startswith(("'", '\"')):
            value = value[1:-1]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise GenerationError(f"Invalid .env variable name on line {line_number}")
        os.environ.setdefault(key, value)


def compare_shape(actual: Any, template: Any, path: str = "$", errors: list[str] | None = None) -> list[str]:
    """Check types, exact object keys and key order against the supplied example."""
    errors = errors if errors is not None else []
    if isinstance(template, dict):
        if not isinstance(actual, dict):
            errors.append(f"{path}: expected object, got {type(actual).__name__}")
            return errors
        expected_keys, actual_keys = list(template), list(actual)
        if expected_keys != actual_keys:
            missing = [k for k in expected_keys if k not in actual]
            extra = [k for k in actual_keys if k not in template]
            reordered = not missing and not extra
            errors.append(f"{path}: object keys {'are reordered' if reordered else 'do not match'}; missing={missing}, extra={extra}")
        for key in expected_keys:
            if key in actual:
                compare_shape(actual[key], template[key], f"{path}.{key}", errors)
    elif isinstance(template, list):
        if not isinstance(actual, list):
            errors.append(f"{path}: expected array, got {type(actual).__name__}")
        elif template:
            for i, item in enumerate(actual):
                compare_shape(item, template[0], f"{path}[{i}]", errors)
    elif template is None:
        if actual is not None:
            errors.append(f"{path}: expected null")
    elif isinstance(template, bool):
        if not isinstance(actual, bool):
            errors.append(f"{path}: expected boolean")
    elif isinstance(template, (int, float)):
        if isinstance(actual, bool) or not isinstance(actual, (int, float)):
            errors.append(f"{path}: expected number")
    elif not isinstance(actual, str):
        errors.append(f"{path}: expected string")
    return errors


def _canonicalize_order(actual: Any, template: Any) -> Any:
    """Keep required keys and template order; discard model-added, out-of-schema keys."""
    if isinstance(actual, dict) and isinstance(template, dict):
        return {key: _canonicalize_order(actual[key], template[key])
                for key in template if key in actual}
    if isinstance(actual, list) and isinstance(template, list) and template:
        return [_canonicalize_order(item, template[0]) for item in actual]
    return actual


def _json_schema_from_shape(shape: Any) -> dict[str, Any]:
    """Build a strict JSON Schema that requires every field in the supplied example."""
    if isinstance(shape, dict):
        properties = {key: _json_schema_from_shape(value) for key, value in shape.items()}
        return {"type": "object", "properties": properties,
                "required": list(shape), "additionalProperties": False}
    if isinstance(shape, list):
        return {"type": "array", "items": _json_schema_from_shape(shape[0]) if shape else {"type": "string"}}
    if isinstance(shape, bool):
        return {"type": "boolean"}
    if isinstance(shape, int):
        return {"type": "integer"}
    if isinstance(shape, float):
        return {"type": "number"}
    if shape is None:
        return {"type": "null"}
    return {"type": "string"}


def validate_course(course: Any, template_doc: dict[str, Any]) -> list[str]:
    errors = compare_shape(course, template_doc)
    if not isinstance(course, dict) or not isinstance(course.get("course"), dict):
        return errors
    c = course["course"]
    modules = c.get("modules")
    if not isinstance(modules, list):
        return errors
    if not 8 <= len(modules) <= 9:
        errors.append(f"$.course.modules: expected 8-9 modules, got {len(modules)}")
    seen_module_ids, seen_module_titles = set(), set()
    all_titles: list[str] = []
    for mi, module in enumerate(modules):
        if not isinstance(module, dict):
            continue
        mid, title = module.get("moduleId"), module.get("title")
        if mid in seen_module_ids:
            errors.append(f"$.course.modules[{mi}].moduleId: duplicate {mid!r}")
        if title in seen_module_titles:
            errors.append(f"$.course.modules[{mi}].title: duplicate {title!r}")
        seen_module_ids.add(mid)
        seen_module_titles.add(title)
        lessons = module.get("lessons")
        if not isinstance(lessons, list):
            continue
        if not 7 <= len(lessons) <= 8:
            errors.append(f"$.course.modules[{mi}].lessons: expected 7-8 lessons, got {len(lessons)}")
        seen_ids, seen_titles = set(), set()
        for li, lesson in enumerate(lessons):
            if not isinstance(lesson, dict):
                continue
            lid, ltitle = lesson.get("lessonId"), lesson.get("title")
            if lid in seen_ids:
                errors.append(f"$.course.modules[{mi}].lessons[{li}].lessonId: duplicate {lid!r}")
            if ltitle in seen_titles:
                errors.append(f"$.course.modules[{mi}].lessons[{li}].title: duplicate {ltitle!r}")
            seen_ids.add(lid)
            seen_titles.add(ltitle)
            if isinstance(ltitle, str):
                all_titles.append(ltitle.casefold().strip())
            if not lesson.get("objective"):
                errors.append(f"$.course.modules[{mi}].lessons[{li}].objective: must not be empty")
            for field in ("example", "quiz", "keyTakeaways"):
                if not lesson.get(field):
                    errors.append(f"$.course.modules[{mi}].lessons[{li}].{field}: must not be empty")
            if isinstance(lesson.get("quiz"), list):
                for qi, q in enumerate(lesson["quiz"]):
                    if not isinstance(q, dict):
                        continue
                    opts, ans = q.get("options"), q.get("correctAnswer")
                    if not isinstance(opts, list) or not opts:
                        errors.append(f"$.course.modules[{mi}].lessons[{li}].quiz[{qi}]: options must be a non-empty array")
                    elif not str(ans).isdigit() or int(ans) >= len(opts):
                        errors.append(f"$.course.modules[{mi}].lessons[{li}].quiz[{qi}]: correctAnswer must index an option")
    duplicates = sorted({t for t in all_titles if all_titles.count(t) > 1})
    for title in duplicates:
        errors.append(f"duplicate lesson title across course: {title}")
    return errors


def _normalize_quiz_answers(course: dict[str, Any]) -> None:
    """Convert answer text or A/B/C labels to the template's string index format."""
    modules = course.get("modules", [])
    for module in modules:
        for lesson in module.get("lessons", []):
            for question in lesson.get("quiz", []):
                options = question.get("options")
                if not isinstance(options, list) or not options:
                    continue
                answer = str(question.get("correctAnswer", "")).strip()
                if answer.isdigit() and int(answer) < len(options):
                    question["correctAnswer"] = str(int(answer))
                    continue
                normalized = answer.strip("` ").casefold()
                matched = next(
                    (index for index, option in enumerate(options)
                     if str(option).strip("` ").casefold() == normalized),
                    None,
                )
                if matched is None and len(answer) == 1 and answer.upper() in "ABCD":
                    candidate = ord(answer.upper()) - ord("A")
                    if candidate < len(options):
                        matched = candidate
                if matched is None and answer.isdigit() and int(answer) == len(options):
                    # Some models give a 1-based final option (e.g. "4" for four choices).
                    matched = len(options) - 1
                if matched is not None:
                    question["correctAnswer"] = str(matched)


def _schema_hint(value: Any) -> Any:
    """Compact key/type guide from the exact supplied JSON example."""
    if isinstance(value, dict):
        return {k: _schema_hint(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_schema_hint(value[0])] if value else []
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def _chunks(items: list[Any], size: int = 4) -> list[list[Any]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _save_checkpoint(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(state, stream, ensure_ascii=False)
        stream.write("\n")
    temporary.replace(path)


def _extract_json(text: str) -> Any:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise GenerationError(f"Model returned malformed JSON: {exc}") from exc


class ChatAPI:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 180,
                 max_completion_tokens: int = 10000):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key, self.model, self.timeout = api_key, model, timeout
        self.max_completion_tokens = max_completion_tokens

    def complete_json(self, system: str, user: str, expected_shape: Any = None) -> Any:
        response_format: dict[str, Any] = {"type": "json_object"}
        if expected_shape is not None and "groq.com" in self.url:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "course_content",
                    "strict": True,
                    "schema": _json_schema_from_shape(expected_shape),
                },
            }
        body = json.dumps({
            "model": self.model,
            "temperature": 0.4,
            "max_completion_tokens": self.max_completion_tokens,
            "response_format": response_format,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }).encode()
        req = urllib.request.Request(self.url, body, {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "course-generator/1.0 (educational project)",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            choice = result["choices"][0]
            if choice.get("finish_reason") == "length":
                raise GenerationError("Model output was truncated (finish_reason=length)")
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise GenerationError("API response has no textual JSON content")
            return _extract_json(content)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1200]
            retry_after = None
            try:
                retry_after = float(exc.headers.get("Retry-After", "")) or None
            except (TypeError, ValueError):
                pass
            raise GenerationError(f"API HTTP {exc.code}: {detail}", retry_after=retry_after) from exc
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError) as exc:
            raise GenerationError(f"API request/response failed: {exc}") from exc


def _module_outline(api: ChatAPI, course_title: str, level: str, duration: str, modules: int, retries: int) -> list[dict[str, Any]]:
    prompt = f"""Plan a {modules}-module course. Return only JSON: {{\"modules\":[{{\"title\":\"...\",\"description\":\"...\",\"lessons\":[\"lesson title\"]}}]}}.
Course: {course_title}; level: {level}; total duration: {duration}.
Use 7 or 8 distinct, progressively ordered lesson titles per module. Avoid overlap and keep all topics relevant. No extra keys."""
    last = ""
    for attempt in range(retries + 1):
        try:
            obj = api.complete_json("You design accurate, coherent course curricula. Return valid JSON only.", prompt)
            plan = obj["modules"]
            if not isinstance(plan, list) or len(plan) != modules:
                raise GenerationError(f"Outline must contain exactly {modules} modules")
            for mod in plan:
                if not isinstance(mod.get("lessons"), list) or not 7 <= len(mod["lessons"]) <= 8:
                    raise GenerationError("Every outline module must have 7-8 lesson titles")
            return plan
        except (GenerationError, KeyError, TypeError) as exc:
            last = str(exc)
            if attempt < retries:
                _retry_wait(attempt, exc)
    raise GenerationError(f"Could not create a valid outline after retries: {last}")


def generate_course(api: ChatAPI, template_doc: dict[str, Any], title: str, level: str, duration: str,
                    module_count: int, retries: int, checkpoint_path: Path | None = None,
                    signature: dict[str, Any] | None = None) -> dict[str, Any]:
    template = template_doc["course"]
    module_example = template["modules"][0]
    lesson_example = module_example["lessons"][0]
    slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-") or "course"
    if checkpoint_path and checkpoint_path.exists():
        state = read_json(checkpoint_path)
        if not isinstance(state, dict) or state.get("signature") != signature:
            raise GenerationError(f"Progress file {checkpoint_path} belongs to different course settings. Use a different --output path for a new course.")
        print(f"Resuming from saved progress: module {state['module_index'] + 1}/{module_count}, batch {state['batch_index'] + 1}.", flush=True)
    else:
        outline = _module_outline(api, title, level, duration, module_count, retries)
        course: dict[str, Any] = {
            "courseId": f"{slug}-v1",
            "courseTitle": title,
            "courseDescription": f"A {level.lower()} course on {title}.",
            "level": level,
            "totalDuration": duration,
            "modules": [],
        }
        state = {"signature": signature, "outline": outline, "course": course,
                 "module_index": 0, "batch_index": 0, "batches": [], "current_module": None}
        _save_checkpoint(checkpoint_path, state)
    outline = state["outline"]
    course = state["course"]
    system = "You are a careful instructional designer. Return complete, valid JSON only. Do not use markdown fences."
    for mi0 in range(state["module_index"], module_count):
        mi = mi0 + 1
        planned = outline[mi0]
        last = ""
        if state["module_index"] == mi0 and state.get("batches"):
            batches = state["batches"]
        else:
            batches = _chunks(planned["lessons"], 4)
            state["batches"] = batches
            state["batch_index"] = 0
            state["current_module"] = None
            _save_checkpoint(checkpoint_path, state)
        generated_module: dict[str, Any] | None = state.get("current_module") if state["module_index"] == mi0 else None
        bi = state["batch_index"] if state["module_index"] == mi0 else 0
        while bi < len(batches):
            lesson_titles = batches[bi]
            print(f"Generating module {mi}/{module_count}, lesson batch {bi+1}/{len(batches)}...", flush=True)
            # Keep each response small enough to avoid provider output truncation.
            if generated_module is None:
                expected = {k: ([lesson_example] * len(lesson_titles) if k == "lessons" else v)
                            for k, v in module_example.items()}
                expected_keys = json.dumps(_schema_hint(expected), ensure_ascii=False)
                target = "the complete module metadata plus its first lesson batch"
            else:
                expected = {"lessons": [lesson_example] * len(lesson_titles)}
                expected_keys = json.dumps(_schema_hint(expected), ensure_ascii=False)
                target = "an object with only the requested lessons array"
            shape_guide = ("" if "groq.com" in getattr(api, "url", "") else
                           f"Exact shape/type guide (an array shows its item shape): {expected_keys}\n")
            prompt = f"""Create {target} as JSON, matching the supplied shape exactly, including every nested key, value type and key order. Return only the JSON object.
Course: {title} | level: {level} | total course duration: {duration}
Module {mi} of {module_count}: {planned['title']}
Module description: {planned['description']}
Generate only these lesson titles, in this order: {json.dumps(lesson_titles, ensure_ascii=False)}
For every requested lesson, provide a clear measurable objective; concise, accurate teaching; two progressive examples with short runnable code where relevant; useful practice; four quiz items with correct indexed answers and brief explanations; and concise activities and solutions for every activity type present in the supplied shape. Keep prose brief and focused while remaining technically accurate for {level}. Keep list item structures and key order exactly as in the shape. Keep identifiers URL-safe. Preserve every key exactly, including the supplied spelling `expectebehavior`.
{shape_guide}
"""
            last = ""
            succeeded = False
            split_worthy = False
            for attempt in range(retries + 1):
                try:
                    generated_part = api.complete_json(system, prompt, expected)
                    # JSON objects are unordered by definition; normalize key order to the owner's template.
                    generated_part = _canonicalize_order(generated_part, expected)
                    errors: list[str] = []
                    compare_shape(generated_part, expected, f"$.course.modules[{mi-1}].batch[{bi}]", errors)
                    if errors:
                        raise GenerationError("Generated content has wrong structure: " + "; ".join(errors[:8]))
                    if len(generated_part["lessons"]) != len(lesson_titles):
                        raise GenerationError(f"Expected {len(lesson_titles)} lessons, got {len(generated_part['lessons'])}")
                    lesson_offset = len(generated_module["lessons"]) if generated_module is not None else 0
                    for li, lesson in enumerate(generated_part["lessons"], start=lesson_offset + 1):
                        lesson["lessonId"] = f"{slug}-mod-{mi}-lesson-{li}"
                        lesson["title"] = planned["lessons"][li-1]
                    if generated_module is None:
                        generated_module = generated_part
                        generated_module["moduleId"] = f"{slug}-mod-{mi}"
                        generated_module["title"] = planned["title"]
                        generated_module["description"] = planned["description"]
                    else:
                        assert generated_module is not None
                        generated_module["lessons"].extend(generated_part["lessons"])
                    state["current_module"] = generated_module
                    state["batches"] = batches
                    state["batch_index"] = bi + 1
                    _save_checkpoint(checkpoint_path, state)
                    succeeded = True
                    break
                except (GenerationError, TypeError, KeyError, IndexError) as exc:
                    last = str(exc)
                    lowered = last.lower()
                    too_large = any(marker in lowered for marker in (
                        "malformed json", "output was truncated", "413 request entity too large",
                        "api http 413", "request too large for model",
                    ))
                    schema_failure = ("json_validate_failed" in lowered or
                                      "failed to validate json" in lowered or
                                      "generated content has wrong structure" in lowered)
                    # Smaller batches help oversized responses. For a single lesson, feed
                    # the provider's concise validation error into the next retry.
                    if (too_large or schema_failure) and len(lesson_titles) > 1:
                        split_worthy = True
                        break
                    if schema_failure:
                        prompt += ("\nYour previous attempt failed this required-schema check: "
                                   + last[:500]
                                   + "\nReturn the complete object again. Include every required key and every required nested key; use concise values, but do not omit fields.\n")
                    elif too_large and attempt < retries:
                        prompt += "\nThe previous output was too long. Return a shorter but complete response, preserving every required key and field.\n"
                    if attempt < retries:
                        _retry_wait(attempt, exc)
            if not succeeded:
                if split_worthy and len(lesson_titles) > 1:
                    middle = len(lesson_titles) // 2
                    batches[bi:bi+1] = [lesson_titles[:middle], lesson_titles[middle:]]
                    state["batches"] = batches
                    _save_checkpoint(checkpoint_path, state)
                    print("The response was truncated or failed JSON validation; splitting this lesson batch and retrying.", flush=True)
                    continue
                raise GenerationError(f"Module {mi}, lesson batch {bi+1} failed after retries: {last}")
            bi += 1
        assert generated_module is not None
        course["modules"].append(generated_module)
        state["module_index"] = mi0 + 1
        state["batch_index"] = 0
        state["batches"] = []
        state["current_module"] = None
        _save_checkpoint(checkpoint_path, state)
    _normalize_quiz_answers(course)
    state["course"] = course
    _save_checkpoint(checkpoint_path, state)
    result = {"course": course}
    errors = validate_course(result, template_doc)
    if errors:
        raise GenerationError("Generated course failed validation:\n- " + "\n- ".join(errors[:30]))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", help="Course title")
    parser.add_argument("--level", choices=["Beginner", "Intermediate", "Advanced"])
    parser.add_argument("--duration", help="Total duration, e.g. '12 hours'")
    parser.add_argument("--modules", type=int, default=8, choices=[8, 9])
    parser.add_argument("--template", type=Path, default=Path("javascript-beginner.json"), help="Owner-provided example JSON")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--retries", type=int, default=3, help="Retries per outline/module (default: 3)")
    parser.add_argument("--validate", action="store_true", help="Validate an existing output file and exit")
    parser.add_argument("--input", type=Path, help="File to validate when using --validate")
    args = parser.parse_args()
    try:
        load_local_env()
        template_doc = read_json(args.template)
        if args.validate:
            if not args.input:
                parser.error("--validate requires --input")
            errors = validate_course(read_json(args.input), template_doc)
            if errors:
                print("INVALID\n" + "\n".join(f"- {e}" for e in errors), file=sys.stderr)
                return 1
            print("Valid course structure, counts, IDs, titles, objectives, examples, quizzes and answer indexes.")
            return 0
        if not args.title or not args.level or not args.duration or not args.output:
            parser.error("generation requires --title, --level, --duration and --output")
        key = os.getenv("AI_API_KEY")
        if not key:
            raise GenerationError("AI_API_KEY is not set. Copy .env.example to .env and configure credentials.")
        api = ChatAPI(os.getenv("AI_BASE_URL", "https://api.openai.com/v1"), key,
                      os.getenv("AI_MODEL", "gpt-4o-mini"))
        template_hash = hashlib.sha256(args.template.read_bytes()).hexdigest()
        signature = {"title": args.title, "level": args.level, "duration": args.duration,
                     "modules": args.modules, "template_sha256": template_hash}
        checkpoint_path = args.output.with_name(args.output.name + ".progress.json")
        generated = generate_course(api, template_doc, args.title, args.level, args.duration,
                                    args.modules, args.retries, checkpoint_path, signature)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(generated, f, ensure_ascii=False, indent=2)
            f.write("\n")
        checkpoint_path.unlink(missing_ok=True)
        print(f"Generated and validated {args.output}")
        return 0
    except GenerationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
