# 승인 후 이미지 생성·전달 계약

현재 리비전이 명확히 승인된 뒤에만 이 문서를 읽고 플러그인 루트의 `scripts/image_workflow.py`를 사용한다. 실제 래스터 생성은 설치된 `imagegen` 스킬의 기본 내장 `image_gen` 도구로 한 장씩 수행한다. Python 스크립트는 모델을 호출하지 않고 승인, 시도 횟수, 파일 검증, 원자적 저장, 재개와 전달 상태만 확정한다.

명령 예시는 플러그인 루트 기준이다. 사용자 작업은 항상 `--project-root`로 분리한다.

## 보고 전에 확정되는 프롬프트

`content_workflow.py create-draft`와 `create-revision`은 장면 프롬프트를 저장하기 전에 다음 규칙을 결정론적으로 반영한다.

- 성경 장면: 사실적인 고대 근동 배경, 중간 밝기, 따뜻한 색감, 영화적 질감
- 현대 장면: 현대 한국 배경, 중간 밝기, 따뜻한 색감, 영화적 질감
- 모든 장면: `9:16`, 상하 자막 안전 여백, 이미지 안의 글자와 워터마크 금지
- 현대 단일 인물: 작업 ID 해시에서 시작해 `20대 남성`, `20대 여성`, `40대 남성`, `40대 여성`, `60대 남성`, `60대 여성` 순환
- 가족·부부·공동체: 여러 연령과 성별을 자연스럽게 사용

입력의 `visual_overrides`는 다음 선택 키만 사용한다.

```json
{
  "style": "watercolor illustration",
  "person": "a Korean woman in her 40s",
  "scene_instructions": {
    "3": "close-up framing"
  }
}
```

장면 지시, 화풍, 인물 지정은 기본값보다 우선한다. `9:16`, 장면 내용, 밝기 요구는 사용자가 명시적으로 바꾼 경우에만 생성 프롬프트 자체에 그 변경을 반영한다. 담당자가 확인하는 `report.md`에는 이 단계가 끝난 최종 프롬프트가 들어가며, 동일 프롬프트의 해시는 `job.json`에 저장된다. 승인 뒤 프롬프트를 보완하거나 다시 쓰지 않는다.

## 승인 게이트 시작

이미지 도구를 호출하기 전에 반드시 실행한다.

```bash
python3 scripts/image_workflow.py start \
  --project-root <project-root> \
  --job-id <job-id>
```

이 명령은 작업 무결성, `APPROVED`, 승인 결정, 현재 리비전, 현재 보고 메시지, 승인 당시 콘텐츠 서명, 승인 뒤 수정·보류 부재를 검사한다. 하나라도 실패하면 상태를 생성 중으로 바꾸지 않으며 ImageGen 호출은 0회다. 통과하면 `GENERATING`을 기록한다. 같은 성공 명령은 상태를 중복 변경하지 않는다.

내장 ImageGen 자체를 사용할 수 없다는 사실을 생성 전에 확인했거나 첫 호출에서 도구가 존재하지 않으면 다음 명령으로 승인을 보존하고 `APPROVED`로 돌아간다.

```bash
python3 scripts/image_workflow.py abort-unavailable \
  --project-root <project-root> \
  --job-id <job-id> \
  --reason "내장 ImageGen 사용 불가"
```

CLI/API 대체 경로로 자동 전환하지 않는다. 사용자가 명시적으로 CLI를 요청한 경우에만 설치된 `imagegen` 스킬의 대체 경로 지침을 따른다.

## 장면별 생성 반복

다음 명령을 실행한다.

```bash
python3 scripts/image_workflow.py next-scene \
  --project-root <project-root> \
  --job-id <job-id>
```

`ready: true`이면 반환된 `prompt_en`을 문자 그대로 사용해 내장 `image_gen`을 정확히 한 번 호출한다. 새로운 이미지이므로 참조 이미지 입력은 사용하지 않는다. 출력의 로컬 파일 경로를 확인한 뒤 다음 명령에 넘긴다. 경로가 반환되지 않으면 생성 결과의 출력 안내에서 정확한 새 파일을 확인하며 추측한 파일을 사용하지 않는다.

```bash
python3 scripts/image_workflow.py record-success \
  --project-root <project-root> \
  --job-id <job-id> \
  --scene-number <number> \
  --source-file <generated-image>
```

도구 호출 또는 파일 검증이 실패하면 성공을 기록하지 말고 다음 명령을 실행한다.

```bash
python3 scripts/image_workflow.py record-failure \
  --project-root <project-root> \
  --job-id <job-id> \
  --scene-number <number> \
  --error "비밀정보를 제외한 실패 원인"
```

- `retry_allowed: true`: `next-scene`을 다시 실행하고 같은 승인 프롬프트로 한 번만 재시도한다.
- 두 번째 실패: 장면은 `FAILED`, 작업은 `NEEDS_REVIEW`다. 추가 ImageGen 호출을 멈춘다.
- `next-scene`을 결과 기록 전에 반복하면 같은 예약 시도를 반환하고 시도 횟수를 더하지 않는다.
- 생성 완료 장면은 건너뛰며 기존 파일을 덮어쓰지 않는다.

`record-success`는 PNG·JPEG·WebP의 실제 컨테이너, 크기, 세로형 구성, 10MB 이하, SHA-256을 검사하고 `revisions/rNNN/images/scene-NNN.<ext>`에 원자적으로 복사한다. 확장자는 실제 형식에서 정하며 입력 파일명은 신뢰하지 않는다.

`ready: false`이면 생성 가능한 장면이 더 없으므로 전달 단계로 간다.

## 부분 실패 재개

두 번 실패한 장면은 수동 검토 뒤 같은 승인 프롬프트로만 재개할 수 있다.

```bash
python3 scripts/image_workflow.py resume-failed \
  --project-root <project-root> \
  --job-id <job-id> \
  --reason "동일 승인 프롬프트 재개" \
  --idempotency-key <unique-key>
```

이 명령은 `FAILED` 장면만 `PENDING`으로 되돌리고 성공·전달 결과는 보존한다. 프롬프트, 원고 또는 장면 경계를 바꾸려면 이 명령을 쓰지 말고 새 리비전을 작성해 다시 승인받는다.

## Telegram 전달과 완료

생성 가능한 장면이 없거나 일부가 `FAILED`가 된 뒤 실행한다.

```bash
python3 scripts/image_workflow.py deliver \
  --project-root <project-root> \
  --job-id <job-id>
```

검증된 `GENERATED` 장면만 번호순으로 전송한다. 전송이 하나라도 실패하면 그 지점에서 멈추고 성공 메시지 ID를 보존한다. 같은 명령을 다시 실행하면 실패 장면부터 번호순으로 재개한다. 이미지 전송이 끝나면 성공 수, 실패 수, 미생성 수와 프로젝트 기준 이미지 폴더를 요약 메시지로 보낸다.

모든 장면이 로컬 저장되고 Telegram에서 `DELIVERED`인 경우에만 작업을 `DELIVERED`로 바꾼다. 생성 실패가 있으면 정상 이미지를 먼저 전달할 수 있지만 작업은 `NEEDS_REVIEW`를 유지한다. Telegram만 실패하면 로컬 장면은 `GENERATED`로 보존하고 이미지 생성은 반복하지 않는다.
