# Evaluation record

## Structural sample check

- Input: supplied `javascript-beginner.json` reference course.
- Result: passed structural validation.
- Counts: 8 modules and 8 lessons per module (64 lessons total).
- Schema: every lesson has the supplied set of nested fields; module and lesson titles/IDs are unique; examples and quizzes are present; quiz answer indexes are in range.

## Offline regression checks

Command: `python -m unittest -v`

Result: 7 tests passed. The cases verify that the supplied sample passes, reordered input keys fail validation, generated keys are normalized to template order and extra model keys are removed, an invalid module count fails, an out-of-range quiz answer fails, lesson batching preserves order, and a generation run resumes from a saved batch.

## Content review finding

The sample declares a 12-hour course, while its module durations total 37 hours (3 modules at 4 hours and 5 modules at 5 hours). Review and reconcile the duration metadata before treating it as a course-quality example.

## Not yet evaluated

- Live generation against a real provider and multiple course topics or levels.
- Code execution and factual accuracy of generated examples.
- Whether generated activities' solutions and quiz explanations are pedagogically correct.
- Rate-limit behavior against a real provider.

These checks need configured model credentials and human content review. The structural tests do not establish course accuracy.
