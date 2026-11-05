# 급식 위젯

교실 전자칠판에서 오늘의 급식과 반별 출발 순서를 보여 주는 Windows 데스크톱 위젯입니다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `widget.pyw` | 화면과 프로그램 동작 |
| `lunch_core.py` | 순서·알레르기 등 계산, 버전(`APP_VERSION`) |
| `tests/` | 자동 테스트 |
| `lunch_widget.spec` | 프로그램 빌드 설정 |
| `packaging/` | 설치 프로그램, 사용법, 배포 도구 |
| `.github/workflows/release.yml` | 자동 빌드와 게시 |

v1.5.6 이하는 예전 저장소(`lunch-gj/lunch`)를 확인하므로, 이 저장소의 새 버전을 알지 못합니다.
기존 사용자는 새 설치 파일을 한 번 직접 설치해야 합니다.

## 개발

```bash
python widget.pyw
```

```bash
python -m unittest discover -s tests
```

개발 중의 설정, 저장 메뉴, 로그는 이 폴더에 저장되며 Git에는 올라가지 않습니다.
설치본은 `%LOCALAPPDATA%\급식 위젯`에 저장합니다.

## 배포

1. `lunch_core.py`의 `APP_VERSION`을 올립니다.
2. GitHub의 Actions 탭에서 **Release → Run workflow**를 누르고 시험판 버전(`v1.6.0-rc1`)을 입력합니다.
3. Releases의 시험판 zip을 전자칠판에서 확인합니다.
4. 같은 방법으로 정식 버전(`v1.6.0`)을 입력해 실행합니다.

사람들에게 공유하는 링크는 항상 같습니다.

```text
https://github.com/gajaessam/lunch-widget/releases/latest/download/lunch-widget.zip
```

## 글꼴

Pretendard는 SIL Open Font License 1.1을 따릅니다. `assets/fonts/Pretendard-OFL-1.1.txt`를 참고하세요.
