# ArchintX AI Course Generator

A reusable command-line course generator that uses an OpenAI-compatible chat completions API. It plans a curriculum, generates complete modules, validates the course against the exact key/type/order shape of the supplied example, and writes accepted JSON only after validation.

## Requirements

- Python 3.10 or newer
- An API key for an OpenAI-compatible model endpoint
- The owner-provided course JSON example as the structural template

The generator uses only the Python standard library. It does not store credentials in source code.

## Setup

1. Create and activate a virtual environment if desired: `python -m venv .venv`
2. Copy `.env.example` to `.env` and set `AI_API_KEY`, `AI_BASE_URL`, and `AI_MODEL`.
3. The script loads simple `KEY=VALUE` lines from `.env` in the current directory. Process environment variables take precedence. On PowerShell, you can instead set them in the current session as shown below.
4. Place the supplied template at `javascript-beginner.json`, or pass its path with `--template`.

Never commit `.env` or share API credentials. The repository does not include a key.

## Generate

```powershell
$env:AI_API_KEY = "your-key"
$env:AI_BASE_URL = "https://api.openai.com/v1"
$env:AI_MODEL = "gpt-4o-mini"
python course_generator.py --title "Python Fundamentals" --level Beginner --duration "12 hours" --template .\javascript-beginner.json --output .\generated\python-beginner.json
```

The pipeline first requests an outline, then generates lessons in batches of up to four. If a response is truncated or malformed, it retries with smaller batches. Temporary API failures use retry/backoff. Progress is saved beside the requested output as `.progress.json`; rerunning the same command resumes it. The checkpoint is removed after a complete validated course is written. Use `--modules 9` for nine modules. Free-tier request quotas may still limit a full course run.

## Validate

```powershell
python course_generator.py --validate --input .\generated\python-beginner.json --template .\javascript-beginner.json
```

The validator checks exact nested object keys, their order, data types, module/lesson counts, duplicate module and lesson titles/IDs, required learning content, and quiz answer indexes. It checks structure and basic consistency; human review is still needed for educational accuracy, code execution, activity correctness, and relevance.

Run the offline regression checks with `python -m unittest -v`.

## Error behavior and limitations

- Missing credentials, API errors, timeouts, malformed JSON, and incomplete modules return a controlled non-zero exit code.
- Failed outline or module responses receive bounded exponential retries, honor a numeric `Retry-After` header, and print each retry wait. The script does not silently save partial courses.
- `AI_BASE_URL` must be the API root ending in `/v1` or the equivalent root; the script appends `/chat/completions`.
- Structured JSON mode is requested. Providers that do not support `response_format: {"type":"json_object"}` may reject the request; use a compatible model endpoint.
- Module-level generation may be expensive and can exceed provider output limits because each lesson has many activities. Increase provider limits or reduce content density in a future schema-approved revision.
- Matching keys and types does not guarantee factually correct lessons or valid code. Review generated content before importing it into ArchintX.

## Project files

- `course_generator.py`: prompt pipeline, API client, generation and validation.
- `javascript-beginner.json`: supplied sample course retained as the reference template and example.
- `test_validation.py`: offline validator regression tests.
- `EVALUATION.md`: observed checks, findings, and remaining evaluation needs.
- `generated/*.progress.json`: temporary resumable progress for an interrupted run; contains course text, never API credentials.
- `.env.example`: safe configuration template with placeholder credentials.
