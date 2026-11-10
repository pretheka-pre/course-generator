"""Offline structural regression checks for the supplied ArchintX example."""
import copy
import json
import re
import tempfile
import unittest
from pathlib import Path

from course_generator import (
    GenerationError,
    _canonicalize_order,
    _chunks,
    compare_shape,
    generate_course,
    validate_course,
)


ROOT = Path(__file__).parent
with (ROOT / "javascript-beginner.json").open(encoding="utf-8") as stream:
    TEMPLATE = json.load(stream)


class FakeCourseAPI:
    def __init__(self, fail_followup_once=False):
        self.fail_followup_once = fail_followup_once

    def complete_json(self, system, prompt, expected_shape=None):
        if prompt.startswith("Plan a "):
            return {"modules": [
                {"title": f"Module {i+1}", "description": f"Description {i+1}",
                 "lessons": [f"Module {i+1} Lesson {j+1}" for j in range(8)]}
                for i in range(8)
            ]}
        module_index = int(re.search(r"Module (\d+) of", prompt).group(1))
        title_line = next(line for line in prompt.splitlines() if line.startswith("Generate only these lesson titles"))
        titles = json.loads(title_line.split(": ", 1)[1])
        first_batch = "complete module metadata plus its first lesson batch" in prompt
        if not first_batch and self.fail_followup_once:
            self.fail_followup_once = False
            raise GenerationError("simulated quota limit")
        lessons = [copy.deepcopy(TEMPLATE["course"]["modules"][0]["lessons"][0]) for _ in titles]
        for lesson, title in zip(lessons, titles):
            lesson["title"] = title
        if first_batch:
            module = copy.deepcopy(TEMPLATE["course"]["modules"][0])
            module["moduleId"] = f"module-{module_index}"
            module["title"] = f"Module {module_index}"
            module["description"] = f"Description {module_index}"
            module["lessons"] = lessons
            return module
        return {"lessons": lessons}


class CourseValidationTests(unittest.TestCase):
    def setUp(self):
        self.course = copy.deepcopy(TEMPLATE)

    def test_supplied_sample_passes(self):
        self.assertEqual(validate_course(self.course, TEMPLATE), [])

    def test_reordered_object_keys_are_rejected(self):
        original = self.course["course"]["modules"][0]
        self.course["course"]["modules"][0] = dict(reversed(list(original.items())))
        errors = validate_course(self.course, TEMPLATE)
        self.assertTrue(any("keys are reordered" in e for e in errors))

    def test_wrong_module_count_is_rejected(self):
        self.course["course"]["modules"].pop()
        errors = validate_course(self.course, TEMPLATE)
        self.assertTrue(any("expected 8-9 modules" in e for e in errors))

    def test_invalid_quiz_answer_index_is_rejected(self):
        quiz = self.course["course"]["modules"][0]["lessons"][0]["quiz"][0]
        quiz["correctAnswer"] = "99"
        errors = validate_course(self.course, TEMPLATE)
        self.assertTrue(any("correctAnswer must index an option" in e for e in errors))

    def test_lesson_batches_are_small_and_keep_order(self):
        batches = _chunks(["a", "b", "c", "d", "e"], 4)
        self.assertEqual(batches, [["a", "b", "c", "d"], ["e"]])

    def test_generated_objects_are_normalized_to_template_key_order(self):
        template = {"analogy": {"subject": "", "comparison": "", "mapping": []}}
        generated = {"analogy": {"mapping": [], "comparison": "text", "subject": "idea", "extra": True},
                     "courseTitle": "unrequested"}
        normalized = _canonicalize_order(generated, template)
        self.assertEqual(list(normalized["analogy"]), ["subject", "comparison", "mapping"])
        self.assertEqual(list(normalized), ["analogy"])
        self.assertEqual(compare_shape(normalized, template), [])

    def test_generation_resumes_from_saved_batch(self):
        signature = {"title": "Python"}
        with tempfile.TemporaryDirectory(dir=ROOT) as temp_dir:
            checkpoint = Path(temp_dir) / "course.progress.json"
            with self.assertRaisesRegex(GenerationError, "simulated quota"):
                generate_course(FakeCourseAPI(fail_followup_once=True), TEMPLATE, "Python", "Beginner",
                                "12 hours", 8, 0, checkpoint, signature)
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(saved["module_index"], 0)
            self.assertEqual(saved["batch_index"], 1)
            self.assertEqual(len(saved["current_module"]["lessons"]), 4)
            result = generate_course(FakeCourseAPI(), TEMPLATE, "Python", "Beginner", "12 hours",
                                     8, 0, checkpoint, signature)
            self.assertEqual(len(result["course"]["modules"]), 8)
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(saved["module_index"], 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
