#!/usr/bin/env python3
"""Standard-library checks for the local content-generation workflow."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

import content_workflow
import image_workflow
import job_store


JOB_ID = "ds-20260812-121314-1234abcd"


def selection(prefix: str) -> dict[str, object]:
    candidates = [{"text": f"{prefix} {number}", "score": 101 - number} for number in range(1, 11)]
    return {
        "selected": candidates[0]["text"],
        "alternatives": [item["text"] for item in candidates[1:4]],
        "candidates": candidates,
    }


def script() -> dict[str, object]:
    return {
        "core_sentence": "말씀을 신뢰하는 사람은 오늘 순종한다.",
        "thumbnail": selection("오늘의 믿음"),
        "opening": selection("나는 무엇을 믿는가"),
        "bridge": "본문은 믿음이 삶으로 이어지는 길을 보여준다.",
        "context": "시인은 복 있는 사람의 길을 악인의 길과 대조한다.",
        "passage_summary": "복 있는 사람은 악한 꾀를 따르지 않는다. 그는 말씀을 즐거워하며 묵상한다.",
        "interpretation_application": "말씀을 사랑한다는 것은 오늘의 선택을 말씀에 맡기는 일이다.",
        "conclusion": "말씀을 신뢰하는 사람은 오늘 순종한다. 나는 오늘 어떤 한 걸음을 내딛겠는가?",
        "prayer": "주님, 말씀을 즐거워하게 하소서. 오늘 한 걸음을 순종하게 하소서.",
        "cta": job_store.FIXED_CTA,
        "full_text": "도구가 다시 계산할 값",
        "estimated_seconds": 1,
        "duration_exception": False,
        "duration_exception_reason": None,
    }


def input_data(*, reference: str | None = "시편 1:1-2") -> dict[str, object]:
    return {
        "passage_text": "복 있는 사람은 악인들의 꾀를 따르지 아니하며 여호와의 율법을 즐거워한다.",
        "passage_reference": reference,
        "title": "복 있는 사람의 길",
        "date": "2026-08-12",
        "special_instructions": [],
        "visual_overrides": None,
    }


def generation(*, current_script: dict[str, object] | None = None) -> dict[str, object]:
    built_script = current_script or script()
    boundaries = content_workflow.segment_script(built_script)
    scenes = []
    for boundary in boundaries:
        section = boundary["section"]
        setting = "biblical_era" if section == "biblical" else "modern"
        scenes.append(
            {
                "source_text": boundary["source_text"],
                "section": section,
                "setting": setting,
                "description_ko": "본문 의미를 충실하게 보여 주는 장면",
                "prompt_en": "A faithful cinematic devotional scene, warm light, vertical 9:16",
            }
        )
    return {
        "content": {
            "devotional_points": {
                "background": "시인은 두 길을 대조한다.",
                "interpretation": "복은 말씀을 사랑하고 따르는 삶에서 드러난다.",
                "core_sentence": "말씀을 신뢰하는 사람은 오늘 순종한다.",
                "application": "오늘의 작은 선택을 말씀 앞에 세운다.",
                "application_questions": ["나는 오늘 어떤 한 걸음을 내딛겠는가?"],
            },
            "script": built_script,
            "scenes": scenes,
        },
        "quality_evaluation": {
            "scores": {
                "passage_fidelity": 5,
                "interpretive_clarity": 5,
                "evangelical_consistency": 4,
                "spoken_naturalness": 5,
                "application_specificity": 5,
            },
            "reasons": {
                "passage_fidelity": "본문의 대조와 명시적 행동에 근거한다.",
                "interpretive_clarity": "본문 요약과 오늘의 적용을 구분한다.",
                "evangelical_consistency": "은혜에 응답하는 순종으로 표현한다.",
                "spoken_naturalness": "짧고 직접적인 낭독 문장을 사용한다.",
                "application_specificity": "오늘 실행할 한 걸음을 묻는다.",
            },
            "immediate_failures": [],
        },
        "verified_sources": [],
        "review_warnings": [],
    }


class ContentWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_20_character_boundaries_and_final_remainder(self) -> None:
        scenes = content_workflow.accumulate_sentence_units(
            [
                ("가" * 19, "biblical"),
                ("나", "biblical"),
                ("다" * 20, "modern_application"),
                ("라" * 21, "prayer"),
                ("마" * 5, "cta"),
            ]
        )
        self.assertEqual([content_workflow.visible_length(item["source_text"]) for item in scenes], [20, 20, 21, 5])
        self.assertEqual([item["section"] for item in scenes], ["biblical", "modern_application", "prayer", "cta"])

    def test_20_character_boundary_ignores_whitespace_and_keeps_12_character_remainder(self) -> None:
        scenes = content_workflow.accumulate_sentence_units(
            [
                ("가 나 다 라 마 바 사 아 자 차 카 타 파 하 거 너 더 러 버 서", "biblical"),
                ("마" * 12, "modern_application"),
            ]
        )
        self.assertEqual(content_workflow.visible_length(scenes[0]["source_text"]), 20)
        self.assertEqual(content_workflow.visible_length(scenes[1]["source_text"]), 12)
        self.assertEqual(scenes[1]["source_text"], "마" * 12)

    def test_prepare_rebuilds_full_text_duration_and_unknown_reference_warning(self) -> None:
        package = content_workflow.prepare_generation(input_data(reference=None), generation())
        script_data = package["content"]["script"]
        self.assertNotIn("도구가 다시 계산할 값", script_data["full_text"])
        self.assertEqual(
            script_data["estimated_seconds"],
            content_workflow.estimate_seconds(script_data["full_text"]),
        )
        self.assertEqual(package["self_check_result"], "통과")
        self.assertIn("본문 위치 미확인: 장절을 추측하지 않고 작성함", package["review_warnings"])
        self.assertEqual(
            set(package["content"]["devotional_points"]),
            {"background", "interpretation", "core_sentence", "application", "application_questions"},
        )
        self.assertEqual(len(package["content"]["script"]["opening"]["candidates"]), 10)
        self.assertEqual(len(package["content"]["script"]["thumbnail"]["candidates"]), 10)
        self.assertEqual(len(package["content"]["script"]["opening"]["alternatives"]), 3)
        self.assertEqual(len(package["content"]["script"]["thumbnail"]["alternatives"]), 3)
        self.assertEqual(
            package["content"]["script"]["full_text"],
            content_workflow.build_full_text(package["content"]["script"]),
        )
        self.assertEqual(
            set(package["content"]["script"]),
            {
                "core_sentence",
                "thumbnail",
                "opening",
                "bridge",
                "context",
                "passage_summary",
                "interpretation_application",
                "conclusion",
                "prayer",
                "cta",
                "full_text",
                "estimated_seconds",
                "duration_exception",
                "duration_exception_reason",
            },
        )

    def test_invalid_candidate_rank_style_source_and_duration_are_rejected(self) -> None:
        wrong_rank = generation()
        wrong_rank["content"]["script"]["opening"]["selected"] = "나는 무엇을 믿는가 2"
        with self.assertRaisesRegex(content_workflow.WorkflowError, "점수 순위"):
            content_workflow.prepare_generation(input_data(), wrong_rank)

        yo_style_script = script()
        yo_style_script["bridge"] = "본문을 함께 살펴보아요."
        with self.assertRaisesRegex(content_workflow.WorkflowError, "요체"):
            content_workflow.prepare_generation(input_data(), generation(current_script=yo_style_script))

        invented_reference_script = script()
        invented_reference_script["bridge"] = "오늘 본문은 21:1-4의 말씀이다."
        with self.assertRaisesRegex(content_workflow.WorkflowError, "장절을 만들어"):
            content_workflow.prepare_generation(
                input_data(reference=None), generation(current_script=invented_reference_script)
            )

        unverified = generation()
        unverified["verified_sources"] = [
            {
                "quote": "낭독문에 없는 직접 인용",
                "author": "저자",
                "source": "책",
                "locator": "https://example.test/source",
                "translated": False,
            }
        ]
        with self.assertRaisesRegex(content_workflow.WorkflowError, "실제 낭독문에 없음"):
            content_workflow.prepare_generation(input_data(), unverified)

        too_long_script = script()
        too_long_script["bridge"] = "가" * 751
        with self.assertRaisesRegex(content_workflow.WorkflowError, "150초를 초과"):
            content_workflow.prepare_generation(input_data(), generation(current_script=too_long_script))

    def test_candidate_duplicates_style_boundaries_and_verified_cross_reference(self) -> None:
        duplicate = generation()
        duplicate["content"]["script"]["opening"]["candidates"][9]["text"] = duplicate[
            "content"
        ]["script"]["opening"]["candidates"][0]["text"]
        with self.assertRaisesRegex(content_workflow.WorkflowError, "중복"):
            content_workflow.prepare_generation(input_data(), duplicate)

        for ending in ("함께 읽어요", "함께 읽어요?", "함께 읽어요!"):
            styled = script()
            styled["bridge"] = ending
            with self.subTest(ending=ending), self.assertRaisesRegex(
                content_workflow.WorkflowError, "요체"
            ):
                content_workflow.prepare_generation(
                    input_data(), generation(current_script=styled)
                )

        allowed = script()
        allowed["bridge"] = "요한복음의 말씀과도 함께 살펴본다."
        content_workflow.prepare_generation(input_data(), generation(current_script=allowed))

        cited = script()
        cited["interpretation_application"] = (
            "하나님의 사랑은 우리에게 먼저 주어졌다(롬 5:8). "
            "그러므로 오늘 그 사랑을 따라 순종한다."
        )
        cited_generation = generation(current_script=cited)
        cited_generation["verified_sources"] = [
            {
                "quote": "롬 5:8",
                "author": "성경",
                "source": "로마서",
                "locator": "로마서 5장 8절",
                "translated": False,
            }
        ]
        package = content_workflow.prepare_generation(input_data(), cited_generation)
        self.assertEqual(package["verified_sources"][0]["quote"], "롬 5:8")

    def test_duration_exception_is_required_only_for_121_to_150_seconds(self) -> None:
        exception_script = script()
        exception_script["bridge"] = "가" * 520
        draft = generation(current_script=exception_script)
        with self.assertRaisesRegex(content_workflow.WorkflowError, "분량 예외와 사유"):
            content_workflow.prepare_generation(input_data(), draft)
        exception_script["duration_exception"] = True
        exception_script["duration_exception_reason"] = "초심자에게 필요한 본문 배경을 보존함"
        package = content_workflow.prepare_generation(
            input_data(), generation(current_script=exception_script)
        )
        seconds = package["content"]["script"]["estimated_seconds"]
        self.assertGreater(seconds, 120)
        self.assertLessEqual(seconds, 150)
        self.assertTrue(any(item.startswith("분량 예외:") for item in package["review_warnings"]))

    def test_quality_gate_rejects_low_axis_low_average_and_immediate_failure(self) -> None:
        low_axis = generation()
        low_axis["quality_evaluation"]["scores"]["passage_fidelity"] = 3
        with self.assertRaisesRegex(content_workflow.WorkflowError, "의미 품질 기준 미달"):
            content_workflow.prepare_generation(input_data(), low_axis)

        low_average = generation()
        low_average["quality_evaluation"]["scores"] = {
            axis: 4 for axis in content_workflow.QUALITY_AXES
        }
        with self.assertRaisesRegex(content_workflow.WorkflowError, "평균 4.2점"):
            content_workflow.prepare_generation(input_data(), low_average)

        immediate_failure = generation()
        immediate_failure["quality_evaluation"]["immediate_failures"] = [
            "본문에 없는 내면 동기를 단정함"
        ]
        with self.assertRaisesRegex(content_workflow.WorkflowError, "즉시 실패"):
            content_workflow.prepare_generation(input_data(), immediate_failure)

    def test_create_draft_renders_matching_report_without_images(self) -> None:
        job = content_workflow.create_draft(
            self.root,
            input_data(),
            generation(),
            job_id=JOB_ID,
        )
        revision = job["revisions"][0]
        root = self.root / "jobs" / JOB_ID
        report = (root / revision["report_path"]).read_text(encoding="utf-8")
        self.assertEqual(job["status"], "DRAFT")
        self.assertEqual(job_store.validate_job(self.root, JOB_ID), [])
        self.assertEqual(revision["quality_evaluation"]["average"], 4.8)
        self.assertIn("passage_fidelity", revision["quality_evaluation"]["reasons"])
        self.assertIn(
            f"승인 요청 · r001 · 약 {revision['script']['estimated_seconds']}초 · "
            f"이미지 {len(revision['scenes'])}장면",
            report,
        )
        self.assertIn("# 큐티 쇼츠 제작안 | 복 있는 사람의 길", report)
        self.assertIn("## 한눈에 보기", report)
        self.assertIn("## 최종 스크립트", report)
        self.assertIn("### 1. 오프닝 질문", report)
        self.assertIn("### 2. 본문 요약", report)
        self.assertIn("### 3. 해석과 적용", report)
        self.assertIn("### 4. 핵심 재언급과 적용 질문", report)
        self.assertIn("### 5. 마무리 기도", report)
        self.assertIn("### 6. 고정 CTA", report)
        self.assertIn("### 공통 제작 기준", report)
        self.assertIn("### SCENE 01 — 본문 의미를 충실하게 보여 주는 장면", report)
        self.assertIn("- 사용 문장: “", report)
        self.assertIn("- English prompt:", report)
        self.assertIn("## 선택 대안", report)
        self.assertIn("## 검토 메모", report)
        self.assertIn("## 승인 방법", report)
        self.assertIn("`/approve` 또는 `/approve@현재봇사용자이름`", report)
        self.assertIn("자체 검수: 통과", report)
        self.assertIn("의미 품질: 통과 · 평균 4.8/5", report)
        self.assertNotIn("방법론 SHA-256", report)
        self.assertNotIn("## 입력 본문", report)
        for field, _section in content_workflow.SPOKEN_SECTIONS:
            value = revision["script"][field]
            if field == "opening":
                value = value["selected"]
            if value:
                self.assertIn(value, report)
        modern_prompts = [
            scene["prompt_en"] for scene in revision["scenes"] if scene["setting"] == "modern"
        ]
        self.assertTrue(all("contemporary Korean setting" in prompt for prompt in modern_prompts))
        self.assertTrue(all("safe top and bottom caption space" in prompt for prompt in modern_prompts))
        self.assertTrue(any(persona in " ".join(modern_prompts) for persona in image_workflow.PERSONAS))
        self.assertTrue(all(scene["image_hash"] is None for scene in revision["scenes"]))
        expected_units = [
            content_workflow._normalized_space(text)
            for text, _section in content_workflow._sentence_units(revision["script"])
        ]
        actual_scene_text = " ".join(scene["source_text"] for scene in revision["scenes"])
        self.assertEqual(actual_scene_text, " ".join(expected_units))
        self.assertNotRegex(report, r"{{[a-z0-9_]+}}")
        self.assertEqual(list((root / "revisions/r001/images").iterdir()), [])

    def test_report_title_falls_back_without_printing_none(self) -> None:
        draft_input = input_data(reference=None)
        draft_input["title"] = None
        package = content_workflow.prepare_generation(draft_input, generation())
        report = content_workflow.render_report(
            package,
            JOB_ID,
            1,
            {"version": "test", "sha256": "0" * 64},
        )
        self.assertIn("# 큐티 쇼츠 제작안 | 오늘의 믿음 1", report)
        self.assertNotIn("제목: 없음", report)

    def test_failure_creates_no_job_and_partial_revision_preserves_sections(self) -> None:
        invalid = generation()
        invalid["content"]["script"]["cta"] = "바뀐 CTA"
        with self.assertRaises(content_workflow.WorkflowError):
            content_workflow.create_draft(self.root, input_data(), invalid, job_id=JOB_ID)
        self.assertFalse((self.root / "jobs" / JOB_ID).exists())

        content_workflow.create_draft(self.root, input_data(), generation(), job_id=JOB_ID)
        revised = generation()
        revised["content"]["devotional_points"]["application"] = "오늘 한 사람을 실제로 돕는다."
        job = content_workflow.create_revision(
            self.root,
            JOB_ID,
            revised,
            feedback=["적용만 구체화한다."],
            idempotency_key=f"{JOB_ID}:2:content",
            preserve_sections=["script.opening", "script.thumbnail", "script.prayer"],
        )
        self.assertEqual(job["current_revision"], 2)
        self.assertEqual(job["revisions"][0]["script"]["opening"], job["revisions"][1]["script"]["opening"])
        self.assertEqual(job_store.validate_job(self.root, JOB_ID), [])
        repeated = content_workflow.create_revision(
            self.root,
            JOB_ID,
            revised,
            feedback=["적용만 구체화한다."],
            idempotency_key=f"{JOB_ID}:2:content",
            preserve_sections=["script.opening", "script.thumbnail", "script.prayer"],
        )
        self.assertEqual(repeated["current_revision"], 2)
        self.assertEqual(len(repeated["revisions"]), 2)
        self.assertFalse((self.root / "jobs" / JOB_ID / "revisions/r003").exists())

        changed_preserved = copy.deepcopy(revised)
        changed_preserved["content"]["script"]["prayer"] = "주님, 새 기도를 드린다."
        changed_preserved["content"]["scenes"] = generation(
            current_script=changed_preserved["content"]["script"]
        )["content"]["scenes"]
        with self.assertRaisesRegex(content_workflow.WorkflowError, "보존 대상으로 지정한"):
            content_workflow.create_revision(
                self.root,
                JOB_ID,
                changed_preserved,
                feedback=["적용만 구체화한다."],
                idempotency_key=f"{JOB_ID}:3:content",
                preserve_sections=["script.prayer"],
            )

    def test_cli_check_reports_no_files(self) -> None:
        input_file = Path(self.temporary.name) / "input.json"
        generation_file = Path(self.temporary.name) / "generation.json"
        input_file.write_text(json.dumps(input_data(), ensure_ascii=False), encoding="utf-8")
        generation_file.write_text(json.dumps(generation(), ensure_ascii=False), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = content_workflow.main(
                ["check", "--input-json", str(input_file), "--generation-json", str(generation_file)]
            )
        self.assertEqual(exit_code, 0)
        checked = json.loads(output.getvalue())
        self.assertEqual(checked["self_check_result"], "통과")
        self.assertEqual(checked["quality_evaluation"]["average"], 4.8)
        self.assertFalse((self.root / "jobs").exists())


if __name__ == "__main__":
    unittest.main()
