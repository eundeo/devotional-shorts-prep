# 세션 07 — 이미지 생성 워크플로 구현 보고서

## 세션 목표

문서 07의 승인 재검증, 승인된 장면 프롬프트의 내장 ImageGen 실행 계약, 이미지 파일 검증·원자적 저장, 장면별 한 번 재시도, 부분 실패 복구, Telegram 순차 전달과 최종 `DELIVERED` 전이를 실제 스킬과 표준 라이브러리 도구로 구현한다.

## 시작 전 이월 점검

- 세션 06 보고서의 `미해결 누락`: 없음
- 기존 전체 회귀 테스트 39개: 재실행 통과
- 아키텍처 자체 검증: 재실행 통과
- 선행 보완 작업: 없음

## 구현 범위

- 보고서 저장 전 성경·현대 장면 공통 스타일과 9:16·자막 여백·글자 금지 확정
- 본문 위치에 따른 일반 고대 근동·족장·광야·철기 이스라엘·포로기·페르시아기·1세기 시대 기본값
- 현대 인물의 작업 ID 기반 연령대·성별 순환
- 화풍, 인물, 장면별 시각 지시의 고정 입력 구조와 우선순위
- 작업·리비전·보고 메시지·승인 결정·콘텐츠 서명·후속 수정·보류를 검사하는 독립 승인 게이트
- ImageGen 호출 전 시도 예약과 결과 기록 전 동일 시도 재사용
- 실제 ImageGen은 설치된 `imagegen` 스킬의 내장 도구로 한 장씩 호출하는 스킬 계약
- PNG·JPEG·WebP 컨테이너 구조와 실제 크기·SHA-256 검사
- 입력 확장자를 신뢰하지 않고 실제 형식에 맞춘 `scene-NNN.<ext>` 저장
- 세로형 구성과 Telegram 사진 제한 10MB 검사
- 같은 디렉터리 임시 파일, `fsync`, 하드 링크를 이용한 무덮어쓰기 원자적 저장
- 장면별 최대 두 번 생성 시도와 두 번째 실패 시 `FAILED`·`NEEDS_REVIEW`
- 수동 검토 후 같은 승인 프롬프트의 실패 장면만 재개
- 생성 성공·전달 성공 장면의 보존과 멱등 처리
- Telegram 첫 실패 지점에서 중지하고 다음 실행에서 번호순 재개
- 전달 결과 요약 메시지와 전체 전달 성공 시에만 `DELIVERED`
- ImageGen 사용 불가 시 승인 데이터를 보존한 `APPROVED` 복구

## 실행 경계

플러그인의 Python 스크립트는 ImageGen 모델을 직접 호출하거나 대체 이미지를 만들지 않는다. `image_workflow.py next-scene`이 승인된 원문·설명·영어 프롬프트와 시도 번호를 반환하고, 스킬이 설치된 `imagegen`의 내장 도구를 정확히 한 번 호출한 뒤 결과 파일을 `record-success` 또는 실패를 `record-failure`로 기록한다. 내장 도구가 없으면 CLI로 몰래 전환하지 않고 `abort-unavailable`로 승인 상태를 보존한다.

이 분리는 다른 사용자가 자신의 Codex 설치에 포함된 ImageGen 권한을 그대로 사용하면서도 로컬 플러그인이 승인·파일·상태 무결성을 결정론적으로 보장하게 한다.

## 변경 파일

- `docs/04-data-and-state-design.md`
- `plugins/devotional-shorts-prep/.codex-plugin/plugin.json`
- `plugins/devotional-shorts-prep/README.md`
- `plugins/devotional-shorts-prep/scripts/content_workflow.py`
- `plugins/devotional-shorts-prep/scripts/image_workflow.py`
- `plugins/devotional-shorts-prep/scripts/job_store.py`
- `plugins/devotional-shorts-prep/scripts/telegram_bot.py`
- `plugins/devotional-shorts-prep/scripts/test_content_workflow.py`
- `plugins/devotional-shorts-prep/scripts/test_image_workflow.py`
- `plugins/devotional-shorts-prep/scripts/test_job_store.py`
- `plugins/devotional-shorts-prep/scripts/test_telegram_workflow.py`
- `plugins/devotional-shorts-prep/scripts/validate_architecture.py`
- `plugins/devotional-shorts-prep/skills/devotional-shorts/SKILL.md`
- `plugins/devotional-shorts-prep/skills/devotional-shorts/references/image-contract.md`
- `plugins/devotional-shorts-prep/skills/devotional-shorts/references/system-boundaries.md`
- `reports/README.md`
- `reports/requirements-traceability.md`
- `reports/session-07-image-generation-workflow.md`

플러그인 버전은 `0.6.0`으로 올렸다. 방법론은 콘텐츠 작성 규칙 `1.0.0`과 기존 해시를 유지한다. 이미지 실행·저장 계약은 방법론을 바꾸지 않으며 최종 프롬프트는 담당자 보고 전에 확정된다.

## 공개 이미지 명령

| 명령 | 구현 결과 |
| --- | --- |
| `start` | 승인과 작업 무결성을 재검증하고 `GENERATING` 시작 |
| `next-scene` | 다음 `PENDING` 장면의 한 번 생성 시도를 예약하고 승인 프롬프트 반환 |
| `record-success` | ImageGen 파일의 형식·크기·해시를 검사해 리비전 폴더에 원자적 저장 |
| `record-failure` | 실패를 기록하고 한 번 재시도 또는 두 번째 실패 상태를 결정 |
| `abort-unavailable` | ImageGen 사용 불가 시 승인 데이터와 장면을 보존한 `APPROVED` 복구 |
| `resume-failed` | 수동 검토 후 `FAILED` 장면만 같은 승인 프롬프트로 재개 |
| `deliver` | 생성 파일을 번호순 전달하고 결과 요약·최종 상태 확정 |

## 문서 07 요구사항 점검

| 항목 | 결과 | 증거 |
| --- | --- | --- |
| 승인 상태를 첫 단계에서 확인 | 통과 | `start`와 저장소 전이 함수의 이중 승인 게이트 |
| 현재 리비전·보고 메시지·콘텐츠 서명 일치 | 통과 | 승인 이벤트 비교와 변조 부정 테스트 |
| 승인 후 수정·보류 차단 | 통과 | 후속 보류 이벤트에서 생성 시도 0회 검사 |
| 20자 장면 경계 유지 | 통과 | 세션 05 분할 결과를 변경하지 않고 승인된 장면 배열만 소비 |
| 장면별 원문·한국어 설명·영어 프롬프트 | 통과 | `next-scene`의 반환 계약과 전방 테스트 |
| 성경·현대 장면 구분 | 통과 | 설정별 스타일과 시대 기본값 회귀 테스트 |
| 중간 밝기·따뜻한 색감·영화적 질감·9:16 | 통과 | 보고 전 결정론적 프롬프트 확정과 보고서 저장 테스트 |
| 현대 인물 연령대·성별 순환 | 통과 | 작업 ID SHA-256 시작값과 연속 현대 장면 비반복 검사 |
| 화풍·인물·장면 지시 우선 | 통과 | 구조화된 `visual_overrides`와 결과 프롬프트 검사 |
| 승인 후 프롬프트 불변 | 통과 | 보고 전에 최종 프롬프트 저장, 승인 후 서명 재검증 |
| ImageGen 순차 호출 계약 | 통과 | 장면 한 건 예약·결과 기록 후 다음 장면 진행 구조 |
| 파일 실제 형식·무결성 검사 | 통과 | PNG CRC·IEND, JPEG SOF·스캔·EOI, WebP RIFF·비트스트림 검사 |
| 파일명과 리비전별 저장 위치 | 통과 | 실제 형식 기반 `scene-NNN.<ext>`와 프로젝트 상대경로 검사 |
| 이미지 해시 저장 | 통과 | `image_hash` 필드와 저장 후 재읽기 검증 |
| 정상 파일 덮어쓰기 금지 | 통과 | 같은 결과 멱등 재실행과 다른 결과 덮어쓰기 거부 테스트 |
| 첫 실패 후 한 번 재시도 | 통과 | 첫 실패→두 번째 성공 및 첫 실패→두 번째 실패 시나리오 |
| 두 번째 실패 `NEEDS_REVIEW` | 통과 | 장면 `FAILED`, 작업 `NEEDS_REVIEW`, 추가 자동 시도 금지 검사 |
| 실패 장면만 재개 | 통과 | 성공 결과 보존과 `FAILED`만 `PENDING` 전환 검사 |
| Telegram 번호순 전달 | 통과 | 첫 전송 실패에서 중지하고 다음 실행이 실패 번호부터 재개 |
| 일부 전달 실패에서 로컬 보존 | 통과 | `GENERATED` 유지와 ImageGen 재호출 없는 전달 재시도 검사 |
| 전달 결과 요약 | 통과 | 성공·실패·미생성 수와 프로젝트 기준 폴더 메시지 검사 |
| 전체 성공만 `DELIVERED` | 통과 | 모든 장면 메시지 ID가 있을 때만 최종 전이하는 종단간 모의 시험 |

## 구현 중 발견하여 세션 안에서 수정한 항목

1. 기존 장면 데이터에는 로컬 이미지 변경을 검증할 `image_hash`가 없었다. 스키마와 검증기에 SHA-256 필드를 추가하고 실제 파일을 다시 읽어 검증하도록 수정했다.
2. 기존 저장소의 `NEEDS_REVIEW → GENERATING` 전이를 직접 호출하면 승인 게이트를 우회할 수 있었다. 저장소 전이와 장면 기록 함수 자체에도 승인 이벤트·보고 메시지·콘텐츠 서명을 검사하도록 보강했다.
3. 결과 기록 전 `next-scene`을 다시 호출하면 생성 시도 횟수가 중복 차감될 수 있었다. 미결과 예약을 식별해 같은 시도와 프롬프트를 반환하도록 수정했다.
4. Telegram 장면 전송이 중간에 실패해도 뒤 장면을 계속 보내면 다음 재시도에서 번호순 전달이 깨질 수 있었다. 첫 실패에서 즉시 멈추고 성공 ID를 보존한 뒤 실패 번호부터 재개하도록 수정했다.
5. 전송 결과 요약의 상태 조합이 같으면 새 실패·재개 사이클의 요약까지 건너뛸 수 있었다. 마지막 이미지 상태 이벤트를 요약 멱등 서명에 포함했다.
6. 특정 화풍 지시를 사용하면 중간 밝기·따뜻한 색감·영화적 질감까지 사라질 수 있었다. 화풍은 사실적 기본만 교체하고 공통 조명·색감·질감은 유지하도록 수정했다.
7. 고대 근동 공통 문구만으로는 명백히 다른 본문 시대를 충분히 반영하지 못했다. 본문 위치가 확인된 경우 일반적인 시대 표현을 선택하고, 미확인일 때는 왕조·도시를 단정하지 않도록 고정했다.
8. `visual_overrides`가 임의 객체라면 사용자 지시를 일관되게 해석할 수 없었다. `style`, `person`, `scene_instructions` 세 필드 계약으로 고정하고 잘못된 필드·장면 번호를 보고 전에 거부했다.

모든 항목은 세션 종료 전에 회귀 테스트로 고정했다.

## 독립 사용 시험

새 에이전트에 플러그인 외부 임시 프로젝트의 승인된 작업과 첫 번째 ImageGen 결과 PNG만 제공했다. 구현의 기대 결과를 알려주지 않았고 추가 ImageGen·Telegram 호출을 금지했다.

- 프로젝트: `/private/tmp/devotional-shorts-session07-forward.hENrV7`
- 작업: `ds-20260812-141904-c47b4d83`, `r001`
- 시작 상태: `APPROVED`, 보고 메시지 `88001`, 승인 결정 `approved`
- 승인·로컬 무결성 검사: 통과
- 공식 순서: `status` → `validate` → `start` → `next-scene` → `record-success` → `status` → `validate`
- 등록 결과: `jobs/ds-20260812-141904-c47b4d83/revisions/r001/images/scene-001.png`
- 이미지: 실제 PNG, 900×1600, 175,530바이트
- SHA-256: `23843803a65188ec940105951bc5da40e69e00a2e1f8bf8e8fd1a12737c7b9d4`
- 최종 상태: `GENERATING`, `GENERATED` 1개, `PENDING` 18개, `FAILED` 0개, `DELIVERED` 0개
- 저장 후 작업 검증: 통과
- 외부 호출: ImageGen 0회, Telegram 0회

주 에이전트가 독립 결과의 상태·작업 검증·파일 형식·크기·해시를 다시 확인했다. 에이전트는 사용자 범위를 지켜 두 번째 장면을 예약하거나 이미지를 전달하지 않았고, 다음 조치만 정확히 안내했다.

## 최종 검증 명령 및 결과

- 표준 `unittest` 전체 회귀 테스트: 51개 통과
- Python 바이트코드 컴파일: 통과
- 아키텍처 자체 검증: 통과 (`plugin_version=0.6.0`, `method_version=1.0.0`)
- 공식 스킬 빠른 검증: 통과 (`Skill is valid!`)
- 공식 플러그인 검증: 통과
- `image_workflow.py --help`: 7개 공개 명령 노출 확인
- `git diff --check`: 통과
- 실행 가능한 문서·소스의 미완성 플레이스홀더: 없음
- 끝 공백, 플러그인 내부 사용자 절대경로: 없음
- 배포 플러그인 내부 생성 이미지와 `jobs/`: 없음
- 독립 작업의 최종 `validate`: 통과

실제 유료 ImageGen과 실제 Bot API 종단간 호출은 이번 세션 종료 검증에서 수행하지 않았다. 생성 도구 호출 자체는 Codex 런타임의 내장 기능이므로 구조와 결과 계약은 독립 PNG와 모의 API로 검증했고, 실제 사용자 자격 증명·담당자 승인·생성 비용이 필요한 운영 종단간 시험은 세션 09의 외부 인수 검증 자료로 구분한다.

## 미해결 누락

- 없음

## 다음 세션 선행 작업

세션 08 시작 전에 51개 전체 회귀 테스트, 아키텍처 검증, 세션 07의 `미해결 누락`을 다시 확인한다. 통과하면 문서 08의 여덟 구현 단계를 실제 파일·테스트 증거와 대조하고, 설치·배포·다른 사용자 검증을 독립 실행 가능한 크기로 구현·감사한다.

## 세션 결론

문서 07의 현재 완료 기준을 모두 충족했다. 현재 리비전의 명확한 승인과 불변 콘텐츠만 ImageGen 입력이 될 수 있고, 이미지 결과는 실제 형식·세로 구성·해시를 검증한 뒤 리비전별 폴더에 원자적으로 보존된다. 장면당 자동 생성은 두 번으로 제한되며, 부분 실패나 Telegram 장애가 성공 결과를 덮어쓰거나 재생성하지 않는다. 모든 장면이 로컬과 Telegram에 전달된 경우에만 `DELIVERED`가 된다. 세션 08로 이월할 구현 누락은 없다.
