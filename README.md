# Devotional Shorts Prep

성경 본문 전문을 한국어 묵상 쇼츠 원고와 장면별 이미지 계획으로 만들고, Telegram 담당자 승인 후 이미지를 생성·전달하는 ChatGPT·Codex 플러그인 저장소다.

현재 릴리스는 플러그인 `1.0.1`, 콘텐츠 방법론 `1.1.0`, 작업 데이터 스키마 `1`이다. macOS에서 자동 검사와 설치를 검증했으며 Windows는 수신자 컴퓨터에서 [Windows 인수 안내](guides/windows-install-and-telegram.md)에 따라 확인한다.

## 제공 기능

- 입력 본문에 근거한 배경·핵심 해석·묵상 문장·적용·질문 생성
- 오프닝과 썸네일 후보 평가, 100~120초 목표의 낭독 원고 작성
- 완결된 문장 기준의 장면 분할과 9:16 이미지 프롬프트 생성
- 로컬 작업·리비전·보고서 저장
- Telegram 보고, 등록 담당자의 현재 메시지 답장 승인 확인
- 승인된 현재 리비전만 이미지 생성·저장·Telegram 전달

영상 편집, 음성·자막 합성, 완성 MP4와 YouTube 업로드는 포함하지 않는다. Telegram 승인 확인은 사용자가 명령할 때 실행되며 상시 감시하지 않는다.

## 빠른 시작

### GitHub에서 설치

이 저장소가 `OWNER/REPOSITORY`에 게시되었다면 다음처럼 설치한다. 안정적인 설치에는 릴리스 태그를 사용한다.

```text
codex plugin marketplace add OWNER/REPOSITORY --ref v1.0.1
codex plugin add devotional-shorts-prep@devotional-shorts
```

설치 결과에서 `1.0.1`, installed, enabled를 확인한 뒤 새 ChatGPT·Codex 작업을 시작한다. 로컬·저장소 마켓플레이스의 지원 범위는 사용하는 ChatGPT·Codex 화면에 따라 다를 수 있다. [OpenAI 플러그인 설치 안내](https://learn.chatgpt.com/docs/plugins)

### ZIP으로 설치

GitHub Release 또는 배포자에게 다음 파일을 함께 받는다.

- `devotional-shorts-prep-1.0.1.zip`
- `devotional-shorts-prep-1.0.1.zip.sha256`
- `marketplace.json`
- [Windows 설치·Telegram 연결 안내](guides/windows-install-and-telegram.md)

Windows 명령, Python 확인, 체크섬 검증과 설치 순서는 위 안내서를 따른다. 다른 운영체제의 기본 설치·업데이트 절차는 [플러그인 README](plugins/devotional-shorts-prep/README.md)에 있다.

## 처음 사용하는 순서

1. Telegram 없이 실제 본문 전문으로 로컬 DRAFT와 검토 보고서를 만든다.
2. 사람이 본문 해석, 적용, 원고, CTA와 이미지 장면을 확인한다.
3. 사용자 전용 Telegram 봇·그룹·승인자를 설정하고 preflight를 통과한다.
4. 테스트 보고서를 보내고 등록 담당자가 최신 요약 메시지에 직접 답장한다.
5. 이미지 도구와 장면 수를 확인한 뒤 승인을 조회하고 이미지를 생성·전달한다.

첫 요청 예시:

> devotional-shorts 스킬로 아래 본문의 제작안을 작성해 주세요. 이번에는 로컬 보고서까지만 만들고 Telegram 전송과 이미지 생성은 실행하지 마세요. 본문: [본문 전문]

실제 토큰과 ID는 문서나 대화에 넣지 않는다. Telegram 설정과 장애 복구, 작업 백업은 [운영 런북](plugins/devotional-shorts-prep/OPERATIONS.md)을 따른다.

## 생성 품질과 변경 범위

기본 설치에는 본문 사실·해석·추론·적용 구분, 후보 평가, 고정 원고 구조, 분량·문체·CTA, 장면과 이미지 규칙이 포함된다. 자동 검사는 이 규칙과 작업 상태를 확인하지만 모든 해석과 이미지 표현을 보증하지 않으므로 게시 전 사람 검토가 필요하다.

대상 독자, 강조점, 화풍, 인물과 특정 장면 구도는 작업별 입력으로 바꿀 수 있다. 기본 원고 구조나 검증 규칙을 지속적으로 바꾸려면 저장소를 fork하고 방법론·계약·관련 검사와 버전을 함께 수정한 뒤 재검증한다. 설치 캐시를 직접 수정하면 업데이트 때 변경을 잃을 수 있다.

## 저장소 구조

| 경로 | 내용 | 일반 사용자가 필요한가 |
| --- | --- | --- |
| `.agents/plugins/` | 저장소 마켓플레이스 등록 정보 | GitHub 설치 시 필요 |
| `plugins/devotional-shorts-prep/` | 실제 배포 플러그인 | 필요 |
| `guides/` | Windows 설치·Telegram 인수 안내 | Windows 사용 시 필요 |
| `docs/` | 요구사항·설계·운영 기준 | 개발·감사 시 참고 |
| `reports/` | 테스트·릴리스·운영 증거 | 배포 검토 시 참고 |
| `scripts/` | 저장소 전체 문서·계약 검사 | 개발·릴리스 시 사용 |
| `dist/` | 로컬에서 만드는 ZIP 산출물 | Git 제외, Release에 별도 첨부 |

각 폴더의 역할은 [guides](guides/README.md), [docs](docs/README.md), [plugins](plugins/README.md), [reports](reports/README.md), [scripts](scripts/README.md)에서 확인할 수 있다.

## 검증

저장소 루트에서 실행한다.

```text
python plugins/devotional-shorts-prep/scripts/validate_architecture.py
python plugins/devotional-shorts-prep/scripts/distribution.py audit
python -m unittest discover -s plugins/devotional-shorts-prep/scripts -p "test_*.py"
python scripts/validate_project.py
```

`1.0.1` 기준은 테스트 71개, 배포 파일 28개, 플러그인·방법론·스키마 일치다. 최신 결과는 [1.0.1 배포 준비 보고서](reports/release-1.0.1.md)에 있다.

## 배포와 보안

- `.env`, 실제 Telegram 값, `jobs/`, 원본 자료와 생성 이미지는 커밋하지 않는다.
- `jobs/`에는 원고와 이미지 외에도 담당자 답장·사용자 ID가 포함될 수 있다.
- `dist/`는 Git에서 제외하므로 검증된 ZIP·체크섬·독립 `marketplace.json`은 GitHub Release 자산으로 따로 올린다.
- 현재 로컬 릴리스 태그는 `v1.0.1`이다. 공개 전에 전체 Git 이력과 릴리스 자산을 다시 확인한다.

변경 내용은 [CHANGELOG](plugins/devotional-shorts-prep/CHANGELOG.md), 배포 인계는 [release-handoff](reports/release-handoff.md)를 따른다.

## 라이선스

[MIT License](LICENSE)
