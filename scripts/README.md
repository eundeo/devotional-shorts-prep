# 저장소 검증 도구

이 폴더는 여러 폴더에 걸친 문서·계약의 일관성을 확인하는 저장소 수준 도구를 보관한다.

- `validate_project.py`: 기획 문서, 세션 보고서, 상대 링크, 상태·명령 계약과 비밀정보 후보를 검사

플러그인 실행·테스트·배포 도구는 `plugins/devotional-shorts-prep/scripts/`에 있다. 저장소 검증은 루트에서 다음처럼 실행한다.

```text
python scripts/validate_project.py
```
