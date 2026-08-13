# 콘텐츠 생성·저장 계약

본문 해석과 문장 작성은 모델이 수행하고, 구조·분량·후보 순위·장면 경계·보고서·저장은 `scripts/content_workflow.py`로 확정한다. 검수 도구를 통과하지 않은 결과를 완성본 또는 담당자 보고본이라고 부르지 않는다.

명령 예시는 플러그인 루트에서 실행하는 경로다. 현재 파일을 기준으로 플러그인 루트는 두 디렉터리 위이며, 다른 작업 디렉터리에서는 `<plugin-root>/scripts/content_workflow.py` 절대경로를 사용한다. 작업 결과는 `--project-root`가 가리키는 프로젝트에 저장하고 플러그인 폴더에는 저장하지 않는다.

## 입력 JSON

다음 키를 모두 사용한다. 선택값이 없으면 `null`, 지시가 없으면 빈 배열을 쓴다. `passage_text`의 원문과 줄바꿈은 바꾸지 않는다.

```json
{
  "passage_text": "본문 전문",
  "passage_reference": null,
  "title": null,
  "date": null,
  "special_instructions": [],
  "visual_overrides": null
}
```

## 생성 JSON

최상위 키는 정확히 `content`, `quality_evaluation`, `verified_sources`, `review_warnings`다.

- `content.devotional_points`: `background`, `interpretation`, `core_sentence`, `application`, `application_questions`를 사용한다.
- `content.script`: `core_sentence`, `thumbnail`, `opening`, `bridge`, `context`, `passage_summary`, `interpretation_application`, `conclusion`, `prayer`, `cta`, `full_text`, `estimated_seconds`, `duration_exception`, `duration_exception_reason`를 사용한다.
- `thumbnail`과 `opening`: `selected`, `alternatives`, `candidates`를 사용한다. 후보는 정확히 10개이며 각 항목은 `{"text": "...", "score": 0}` 형식이다. 점수가 같으면 [`method.md`](method.md)의 세부 우선순위로 먼저 정렬한다. `selected`는 1위, `alternatives`는 2~4위여야 한다.
- `full_text`와 `estimated_seconds`: 임시값을 넣을 수 있으나 도구가 고정 낭독 순서로 다시 계산한다.
- `duration_exception`: 120초 이하는 `false`, 121~150초는 필수 배경을 보존해야 하는 경우에만 `true`다. 예외이면 한 문장 사유를 넣는다.
- `content.scenes`: 먼저 아래 `segment` 명령으로 계산한 `source_text`와 `section`을 문자 그대로 사용한다. 각 항목에 `setting`, `description_ko`, `prompt_en`을 보탠다. 실제 이미지 파일은 만들지 않는다.
- `quality_evaluation`: `scores`, `reasons`, `immediate_failures`를 사용한다. `scores`와 `reasons`의 키는 정확히 `passage_fidelity`, `interpretive_clarity`, `evangelical_consistency`, `spoken_naturalness`, `application_specificity`다. 점수는 1~5점 정수, 근거는 비어 있지 않은 한 문장, 즉시 실패 조건이 없으면 `immediate_failures`는 빈 배열이다. 한 축이라도 4점 미만이거나 평균이 4.2점 미만이거나 배열이 비어 있지 않으면 `check`와 저장이 실패한다.
- `verified_sources`: 보조 성경 장절과 직접 인용이 없으면 빈 배열이다. 있으면 `quote`, `author`, `source`, `locator`, `translated`를 모두 기록한다. 보조 성경 구절은 `quote`에 낭독문에 사용한 장절 또는 구절 조각, `author`에 `성경`, `source`에 성경 이름, `locator`에 검증한 장절을 기록할 수 있다. 원문과 출처를 확인하지 못한 인용은 삭제하고 연결 문장을 다시 쓴다.
- `review_warnings`: 사용자 지시 충돌이나 사람의 확인이 필요한 사실을 기록한다. 본문 위치 미확인과 분량 예외는 도구가 자동으로 추가한다.

## 장면 경계 생성

스크립트 JSON을 만든 다음 실행한다.

```bash
python3 scripts/content_workflow.py segment --script-json <script.json>
```

도구는 실제 낭독 섹션의 완결 문장을 순서대로 누적하며, 공백 제외 20자 이상이 되는 순간 장면을 확정한다. 마지막 누적분은 20자 미만이어도 남긴다. 출력된 원문을 줄이거나 늘리지 않는다.

장면 구분은 다음 값을 사용한다.

- `biblical`: 본문 배경과 본문 요약
- `interpretation`: 본문 연결과 해석
- `modern_application`: 오프닝, 현대 적용, 결론
- `prayer`: 기도
- `cta`: 고정 CTA

`biblical`은 `biblical_era`, `modern_application`·`prayer`·`cta`는 `modern`으로 설정한다. `interpretation`은 설명의 실제 시각 배경에 맞춰 둘 중 하나를 선택한다.

## 새 초안 실행

1. [`method.md`](method.md)를 처음부터 끝까지 읽는다.
2. 입력 JSON과 생성 JSON을 준비한다.
3. [`quality-evaluation.md`](quality-evaluation.md)의 다섯 축을 평가한다.
4. 한 축이 4점 미만이거나 평균이 4.2점 미만이면 실패한 축만 한 번 수정한다.
5. 아래 결정적 자체 검수를 실행한다.
6. 의미 품질이나 결정적 검수가 실패하면 해당 항목만 한 번 수정하고 둘을 모두 다시 실행한다.
7. 다시 실패하면 파일을 저장하거나 텔레그램에 보고하지 말고 실패 항목과 복구 방법을 한국어로 알린다.
8. 모두 통과하면 `create-draft`를 실행한다.

```bash
python3 scripts/content_workflow.py check \
  --input-json <input.json> \
  --generation-json <generation.json>

python3 scripts/content_workflow.py create-draft \
  --project-root <project-root> \
  --input-json <input.json> \
  --generation-json <generation.json>
```

성공 출력의 작업 ID, 리비전, 상태, 예상 시간, 장면 수, 보고서 경로를 확인한다. `job.json`, `report.md`, `method-snapshot.md`가 다시 검증된 뒤에만 `DRAFT`가 반환된다. 텔레그램 전송은 다음 단계의 별도 명령이며 이 도구가 수행하지 않는다.

## 담당자 보고서 구성

`report.md`는 담당자가 바로 제작안을 읽고 승인할 수 있도록 다음 순서를 고정한다.

1. 제목, 리비전, 예상 낭독 시간, 장면 수와 작업 ID
2. 핵심 문장, 최종 오프닝 질문, 최종 썸네일 문구
3. 오프닝·본문 요약·해석과 적용·결론과 질문·기도·고정 CTA로 나눈 최종 스크립트
4. 공통 제작 기준과 장면별 사용 문장·한국어 시각 설명·최종 English prompt
5. 오프닝·썸네일 대안 각 3개
6. 검수·분량·출처·주의사항의 짧은 요약
7. 현재 텔레그램 요약 메시지에 직접 답장하는 승인 방법

입력 본문 전문, 묵상 포인트의 중간 분석, 의미 품질 축별 점수·상세 근거, 방법론 버전·해시는 `job.json`과 `method-snapshot.md`에 보존하고 보고서 본문에는 반복하지 않는다. 제목이나 본문 위치가 없어도 `없음`을 제목으로 출력하지 않으며 썸네일 최종안을 제목 대체값으로 사용한다. 장면 제목은 한국어 시각 설명에서 결정론적으로 만들고, 보고서의 English prompt는 저장·승인되는 최종 프롬프트와 같아야 한다.

## 수정 리비전 실행

1. 현재 `job.json`과 현재 `report.md`를 먼저 읽는다.
2. 수정 대상과 문자 그대로 보존할 필드를 정한다.
3. 핵심 의미가 바뀌면 `--full-regeneration`을 사용해 해석, 적용, 오프닝, 썸네일, 스크립트, 장면을 다시 만든다.
4. 좁은 변경이면 비대상 필드의 값을 그대로 복사하고 각 경로를 `--preserve-section`으로 넘긴다.
5. 새 생성 JSON 전체를 다시 검수하고 새 리비전을 만든다.

```bash
python3 scripts/content_workflow.py create-revision \
  --project-root <project-root> \
  --job-id <job-id> \
  --generation-json <generation.json> \
  --feedback "담당자 수정 의견" \
  --idempotency-key "<고유 키>" \
  --preserve-section script.opening
```

보존 대상으로 표시한 값이 바뀌면 도구가 새 리비전을 만들지 않는다. `--preserve-section`과 `--full-regeneration`은 동시에 사용할 수 없다. 이전 리비전과 이전 보고서는 변경하지 않으며 새 리비전은 다시 승인받아야 한다.

## 검수 실패 의미

- 장절이 없으면 추측하지 않고 경고와 함께 계속한다.
- 후보 순위, 필수 필드, 장면 원문, 시대 구분, 고정 CTA가 틀리면 저장 전에 중단한다.
- `요`체 종결이 있으면 해당 문장만 `다·나·까` 문체로 고친다.
- 150초 초과면 중복 설명, 수식어, 반복 적용 순으로 압축한다.
- 직접 인용이 실제 낭독문에 없거나 출처 정보가 불완전하면 인용을 삭제하고 다시 검수한다.
- 저장 후 읽기 검증이 실패하면 `NEEDS_REVIEW`로 표시하고 텔레그램 전송을 금지한다.
