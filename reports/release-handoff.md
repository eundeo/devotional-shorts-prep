# 묵상 쇼츠 자동화 플러그인 배포 인계 보고서

## 현재 판정

플러그인 `1.0.1`의 내부 수정, 자동 검사, 신규 설치, 기존 `1.0.0` 업데이트, 배포 ZIP 준비가 완료됐다. 다른 사용자는 ZIP 또는 비공개 GitHub 저장소 `eundeo/devotional-shorts-prep`으로 설치할 수 있다. 실제 Telegram 운영은 각 사용자의 환경변수와 테스트 그룹 인수가 필요하다.

2026-09-09 기준 [비공개 저장소](https://github.com/eundeo/devotional-shorts-prep)에 전체 소스와 `v1.0.1` 태그를 게시했고, [GitHub Release](https://github.com/eundeo/devotional-shorts-prep/releases/tag/v1.0.1)에 배포 파일과 Windows 안내서를 첨부했다.

현재 사용자 Codex에는 `1.0.1` 설치·활성화와 설치 캐시 재검증까지 완료했다. 새 Codex 작업부터 갱신된 스킬을 사용한다.

- 플러그인: `devotional-shorts-prep` `1.0.1`
- 방법론: `1.1.0`
- 방법론 SHA-256: `1684b969e1822d2cd41eb7a485201871339cd04203557ff13e2b684bb6847ae5`
- 데이터 스키마: `1`
- 테스트: 71/71 통과
- 배포 경계: 28개 파일 통과
- ZIP SHA-256: `9300995ec988aa85a43b187be6adf862fa93faff35793107275d0d27eeb65c84`

## 전달할 산출물

- [Windows 설치·Telegram 연결·제작 방식 변경 안내](../guides/windows-install-and-telegram.md) — ZIP 외부에 함께 전달하거나 GitHub 기본 브랜치·릴리스에 첨부

- [1.0.1 배포 준비 보고서](release-1.0.1.md)
- [사용 준비·배포·운영 보완 결과](operations-readiness-2026-09-08.md)
- [요구사항 추적표](requirements-traceability.md)
- [플러그인 README](../plugins/devotional-shorts-prep/README.md)
- [독립 운영서](../plugins/devotional-shorts-prep/OPERATIONS.md)
- [변경 이력](../plugins/devotional-shorts-prep/CHANGELOG.md)
- [배포 ZIP](../dist/1.0.1/devotional-shorts-prep-1.0.1.zip)
- [ZIP 체크섬](../dist/1.0.1/devotional-shorts-prep-1.0.1.zip.sha256)
- [독립 marketplace.json](../dist/1.0.1/marketplace.json)

`dist/`는 Git 추적 대상이 아니므로 ZIP·체크섬·매니페스트는 Git 커밋과 별도로 보관하고 릴리스 자산으로 전달한다.

## 인계 순서

1. 수신자가 체크섬을 확인하고 README 절차로 설치한다.
2. 설치 후 새 Codex 작업을 시작하고 로컬 DRAFT 생성까지 확인한다.
3. 운영자는 비밀 환경변수를 로컬 실행 환경에 설정한다.
4. 운영서의 Telegram 테스트 그룹 인수 절차를 통과시킨다.
5. 운영 데이터 담당자와 보존·백업 위치를 기록한다.
6. 공개 배포 시 지정된 원격 저장소에 태그와 릴리스를 게시한다.

## 변경 경계

이번 릴리스는 운영 안전성과 배포 완결성을 보완한다. 콘텐츠 생성 방법론과 작업 데이터 스키마는 변경하지 않았으므로 기존 작업은 저장된 방법론 스냅샷으로 계속 열린다. 자동 웹훅 삭제, 상시 승인 감시, 자동 작업 삭제, YouTube 업로드와 영상 렌더링은 범위 밖이다.

이전 `1.0.0`의 상세 이력과 2026-08-13 당시 Telegram 종단간 인수 기록은 [1.0.0 배포 보고서](release-1.0.0.md)와 [세션 09 보고서](session-09-test-and-acceptance.md)에 보존한다.
