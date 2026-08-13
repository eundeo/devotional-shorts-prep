# Devotional Shorts Prep

현재 릴리스는 `1.0.0`이며 [MIT 라이선스](LICENSE)로 배포한다. 콘텐츠 방법론은 `1.1.0`, 작업 데이터 스키마는 `1`이다.

묵상 본문 전문을 묵상 포인트, 쇼츠 낭독 원고, 장면별 이미지 계획으로 변환하고 담당자 승인 후 이미지를 생성·전달하는 Codex 플러그인이다.

## v1 경계

- 하나의 `devotional-shorts` 스킬로 사용자 흐름을 조정한다.
- 작업 데이터는 플러그인 밖의 프로젝트 `jobs/`에 리비전별로 저장한다.
- 텔레그램 연동은 상시 서버 없이 사용자가 실행하는 명령으로만 동작한다.
- 현재 리비전에 대한 등록 담당자의 명확한 승인 전에는 이미지를 생성하지 않는다.
- 봇 토큰과 실제 사용자·그룹 ID는 환경변수에서만 읽는다.

웹 UI, 데이터베이스, 웹훅, 유튜브 업로드, 음성 합성, 자막 및 영상 편집은 v1 범위가 아니다.

## 구성요소

| 구성요소 | 책임 |
| --- | --- |
| `skills/devotional-shorts/SKILL.md` | 유일한 사용자 진입점과 작업 흐름 조정 |
| `skills/devotional-shorts/references/` | 버전이 있는 제작 방식, 승인 예시, 시스템 경계 |
| `skills/devotional-shorts/templates/report.md` | 담당자 검토 보고서 구조 |
| `scripts/content_workflow.py` | 의미 품질·구조 검수, 20자 장면 분할, 보고서 렌더링과 `DRAFT` 저장 |
| `scripts/job_store.py` | 작업·리비전·상태를 원자적으로 저장하는 로컬 도구 |
| `scripts/telegram_bot.py` | Telegram 설정 점검, 보고·답장·이미지 API 입출력 |
| `scripts/approval_workflow.py` | 유효 답장 분류·집계와 승인·상태 사용자 명령 |
| `scripts/image_workflow.py` | 승인 재검증, ImageGen 결과 검증·저장, 실패 재개와 전달 완료 |
| 프로젝트의 `jobs/` | 작업·리비전·방법론 스냅샷·생성 이미지 |

플러그인은 콘텐츠를 결정하고 외부 도구는 결정된 요청만 수행한다. 텔레그램 도구는 답장 의미를 판정하지 않으며 ImageGen은 승인 상태를 판정하지 않는다. 스킬은 현재 리비전의 명확한 승인을 검증한 단일 경로를 통해서만 이미지 생성을 시작한다.

## 사용자 명령

- `$devotional-shorts`와 묵상 본문 전문: 제작안 생성 및 담당자 보고
- `승인 확인 [작업 ID]`: 현재 보고 메시지의 답장 조회
- `보고 재전송 [작업 ID]`: 현재 리비전 보고서 재전송
- `작업 상태 [작업 ID]`: 저장·보고·승인·이미지 상태 확인
- 승인된 작업의 이미지 생성: 승인 게이트 통과 후 장면별 내장 ImageGen 실행과 전달
- 실패 이미지 재생성: 같은 승인 프롬프트로 실패 장면만 재개

## 보안

`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_APPROVER_IDS`의 실제 값은 저장소에 추가하지 않는다. 배포물에는 작업 결과인 `jobs/`와 검토 전 원본 자료인 `source-materials/`를 포함하지 않는다.

## 설치와 공유

### GitHub 또는 로컬 체크아웃

이 저장소는 비기본 로컬 마켓플레이스 `devotional-shorts`를 포함한다. 저장소 루트에서 플러그인 구조와 배포 경계를 먼저 검사한 뒤 Codex 공식 명령으로 등록한다.

```bash
python3 plugins/devotional-shorts-prep/scripts/validate_architecture.py
python3 plugins/devotional-shorts-prep/scripts/distribution.py audit
codex plugin marketplace add <저장소-루트-또는-owner/repo>
codex plugin add devotional-shorts-prep@devotional-shorts
```

설치 후 새 Codex 작업을 시작해야 새 스킬과 도구가 확실히 로드된다. 저장소를 공유할 때는 프로젝트 `jobs/`, `source-materials/`, `.env`를 포함하지 않는다.

### ZIP 배포

개발자는 저장소 밖의 출력 폴더에 결정적 ZIP, 체크섬, 마켓플레이스 매니페스트를 만든다.

```bash
python3 plugins/devotional-shorts-prep/scripts/distribution.py build --output-dir <배포-출력-폴더>
python3 plugins/devotional-shorts-prep/scripts/distribution.py verify --archive <배포-출력-폴더>/devotional-shorts-prep-1.0.0.zip
```

수신자는 배포 파일이 있는 폴더에서 함께 받은 `.zip.sha256`으로 ZIP을 확인하고, 전용 마켓플레이스 루트의 `plugins/` 아래에 압축을 푼다. 함께 받은 `marketplace.json`은 같은 루트의 `.agents/plugins/marketplace.json`으로 둔다. 그 후 해당 루트를 등록하고 플러그인을 설치한다.

```bash
shasum -a 256 -c devotional-shorts-prep-1.0.0.zip.sha256
python3 -m zipfile -e devotional-shorts-prep-1.0.0.zip <마켓플레이스-루트>/plugins
python3 <마켓플레이스-루트>/plugins/devotional-shorts-prep/scripts/distribution.py audit
codex plugin marketplace add <마켓플레이스-루트>
codex plugin add devotional-shorts-prep@devotional-shorts
```

`marketplace.json`을 사용자 전역 설정에 직접 합치지 않는다. 별도 마켓플레이스 루트를 등록하면 기존 개인 마켓플레이스와 충돌하지 않는다.

### 설정과 최초 진단

`.env.example`은 변수 이름만 설명하며 실제 값을 저장하지 않는다. 각 사용자는 세 값을 자신의 셸 환경변수로 설정한 뒤 다음 순서로 진단한다.

```bash
python3 <설치된-플러그인>/scripts/telegram_bot.py preflight
python3 <설치된-플러그인>/scripts/content_workflow.py --help
python3 <설치된-플러그인>/scripts/approval_workflow.py status --help
```

`preflight`가 성공하기 전에는 보고 전송을 시도하지 않는다. 설정값은 README, 작업 JSON, 보고서, 오류 로그 또는 배포 압축에 복사하지 않는다.

### 업데이트와 재설치

배포 버전 업데이트는 새 ZIP을 먼저 검증하고 기존 설치본보다 낮은 버전이 아닌지 확인한 뒤 원자적으로 교체한다. 작업 데이터는 플러그인 밖의 프로젝트 `jobs/`에 있어 교체 대상이 아니다.

```bash
python3 <현재-설치본>/scripts/distribution.py verify --archive <새-ZIP>
python3 <현재-설치본>/scripts/distribution.py extract --archive <새-ZIP> --destination <마켓플레이스-루트>/plugins --update
codex plugin add devotional-shorts-prep@devotional-shorts
```

같은 버전을 로컬에서 반복 개발할 때만 `plugin-creator`의 `update_plugin_cachebuster.py`로 매니페스트 캐시버스터를 갱신하고 재설치한다. 설치 제거는 `codex plugin remove devotional-shorts-prep@devotional-shorts`를 사용한다. 업데이트나 재설치 후에는 새 Codex 작업에서 확인한다.

버전별 변경 내용과 호환성은 [CHANGELOG.md](CHANGELOG.md)에서 확인한다.

## 개발 기준

프로젝트의 [`docs`](../../docs/README.md)를 번호순으로 구현한다. 각 문서 구현이 끝날 때 `reports/`에 세션 검증 보고서를 남기며, 미해결 누락은 다음 세션의 기능 작업보다 먼저 처리한다.

아키텍처 자체 점검은 다음 명령으로 실행한다.

```bash
python3 scripts/validate_architecture.py
```

작업 저장 도구의 명령은 다음과 같이 확인한다.

```bash
python3 scripts/job_store.py --help
```

콘텐츠 워크플로 명령은 다음과 같이 확인한다.

```bash
python3 scripts/content_workflow.py --help
```

Telegram과 승인 명령은 다음과 같이 확인한다.

```bash
python3 scripts/telegram_bot.py --help
python3 scripts/approval_workflow.py --help
```

승인 후 이미지 명령은 다음과 같이 확인한다.

```bash
python3 scripts/image_workflow.py --help
```

배포 경계와 패키지 명령은 다음과 같이 확인한다.

```bash
python3 scripts/distribution.py --help
python3 scripts/distribution.py audit
```
