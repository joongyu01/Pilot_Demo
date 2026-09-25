# Carrot Web Live Demo

**[데모 열기 · Open the demo](https://www.joongyu.co.kr/Pilot_Demo/)**

[ajouatom/openpilot](https://github.com/ajouatom/openpilot) `carrot-wip` 브랜치의 Carrot Web을 기기 없이 브라우저에서 실행하는 비공식 데모입니다.
화면을 흉내 내 다시 만든 것이 아니라, **upstream 코드를 그대로** 돌립니다.

- 웹 UI: upstream `selfdrive/carrot/web`을 upstream 빌드 스크립트(`npm run build`)로 빌드한 결과물
- 서버: upstream `selfdrive/carrot/server`(aiohttp)를 [Pyodide](https://pyodide.org)로 브라우저 안에서 실행하고, 모든 `/api/*` 요청을 실제 라우트 핸들러가 처리

그래서 설정 카탈로그, 차량 선택, 첫 설치 인트로, 값 범위 검사, 변경 이력, 프로필, 백업/복원이 기기에서와 같은 코드로 동작합니다.

## 자동 업데이트

GitHub Actions가 매시간 `carrot-wip`의 최신 커밋을 확인합니다. 바뀌었으면 다음 순서로 진행합니다.

1. upstream에서 웹과 서버에 필요한 경로만 sparse clone
2. 웹 빌드 → 사이트 조립 (`tools/build_site.py`)
3. 백엔드 검사: 서버 코드를 네이티브 Python으로 부팅해 핵심 API 확인 (`tools/smoke_backend.py`)
4. 브라우저 검사: Chromium에서 데모를 실제로 띄워 인트로, 설정, 저장, 경로 누수 확인 (`tests/e2e.mjs`)
5. 통과하면 GitHub Pages에 배포하고, 배포한 upstream 커밋을 `state/upstream.json`에 기록

검사에 실패하면 배포하지 않습니다. 직전 정상 버전이 계속 서비스되고, 실패한 실행은 Actions 탭에 남습니다.

## 설정 공유 (설정 불러오기(web))

화면 맨 위 **설정 불러오기(web)**에서 다른 사람이 올린 설정을 차종별로 보고, 바뀌는 값을 미리 본 뒤 적용할 수 있습니다. 적용은 도구 → QR Restore와 같은 upstream 복원 API(`params_restore_preview` → `params_restore_json`)를 거칩니다. **JSON 받기**로 기기용 백업 파일도 받을 수 있습니다.

올리는 방법: `DEMO` → **내 설정 올리기** → 백업 JSON 선택(또는 지금 데모 설정) → 닉네임·차종·날짜·메모 입력 → **GitHub에 올리기**. 이슈 양식이 채워진 채로 열리고, **Create**를 누르면 봇(`share.yml`)이 확인합니다.

- 웹에서 편집할 수 있는 설정(카탈로그 + 디바이스 토글) 중 **기본값과 다른 값만** 저장합니다. 주행 기록, 시간대, 약관 동의, 차량 선택 같은 나머지 키는 버립니다.
- 범위를 벗어난 값은 빼고, 차종은 데모 차량 목록에 있어야 합니다.
- 통과하면 `shared/presets/<이슈 번호>.json`으로 커밋되고 데모가 다시 배포됩니다(1~3분). 이슈를 고치면 같은 항목이 갱신됩니다.
- 닉네임·차종·날짜·메모와 이슈를 올린 GitHub 계정이 공개됩니다.

## 할 수 있는 것 / 없는 것

| 동작함 | 기기가 있어야 함 (데모에서는 비어 있음) |
|---|---|
| 설정 탐색·검색·값 변경·기본값 | 주행 화면의 실시간 영상·HUD 값 |
| 차량 선택 (opendbc 차량 목록) | 대시캠·화면녹화·로그 |
| 첫 설치 인트로 마법사 | 터미널 |
| 즐겨찾기·프로필·변경 이력 | git·재부팅 같은 기기 명령의 실제 효과 |
| 도구 → Backup / Restore (기기와 같은 JSON) | 차종별 인기값 (당근 서버 조회) |

## 개인정보

- 바꾼 값은 **이 브라우저의 IndexedDB에만** 저장됩니다. 화면 맨 위 가운데 `DEMO` → `데모 초기화`로 지울 수 있습니다.
- 데모 서버 안에서는 외부 네트워크(aiohttp, urllib, socket, subprocess)를 모두 막아 두었습니다.
- 외부로 나가는 요청은 GitHub Pages와 Pyodide 런타임(jsDelivr CDN)뿐입니다.

## 로컬 빌드

```bash
# upstream 체크아웃(웹 빌드 완료 상태)이 ./upstream 에 있다고 가정
pip install aiohttp numpy pycapnp
python tools/build_site.py --upstream upstream --out _site
python tools/smoke_backend.py _site/_demo/backend.zip
npm ci && npx playwright install chromium && node tests/e2e.mjs _site
python tools/serve.py 8765   # http://localhost:8765/Pilot_Demo/
```

## 구조

| 경로 | 역할 |
|---|---|
| `demo/boot.js` | 페이지 쪽. `/api/*`를 백엔드 Worker로 보내고, 루트 절대경로를 Pages 하위 경로로 바꾸며, 서버가 렌더링한 앱을 띄움 |
| `demo/backend-worker.js` | Pyodide Worker. upstream 서버 코드를 풀고, IndexedDB에 상태를 저장 |
| `demo/backend/demo_backend.py` | upstream 라우트를 등록하고 요청을 실제 핸들러로 전달. 기기 전용 모듈만 대체 |
| `demo/backend/params_pyx.py` | `params_keys.h`를 읽는 순수 Python Params (타입·기본값·캐스팅 규칙은 `params_pyx.pyx`와 동일) |
| `tools/build_site.py` | 사이트 조립. 서버 패키지의 import 폐포만 `backend.zip`에 담고, 공유 스키마(`shared/schema.json`)를 만듦 |
| `demo/share.js` | 설정 불러오기(web) / 내 설정 올리기 화면 |
| `tools/share_settings.py` | 공유 봇의 검사기. 이슈 양식 → 프리셋 JSON |

## 크레딧

- Carrot Web / CarrotPilot: [ajouatom/openpilot](https://github.com/ajouatom/openpilot)
- openpilot: comma.ai, MIT License (사이트의 `LICENSE-openpilot.txt`)
- 오프라인 설정 데모라는 아이디어: [fullmetalsonic/webcarrot-offline-demo](https://github.com/fullmetalsonic/webcarrot-offline-demo)

공식 배포물이 아닙니다. 데모에서 만든 백업을 기기에 복원할 때는 내용을 직접 확인하세요.

---

**English** — An unofficial live demo of Carrot Web from `ajouatom/openpilot@carrot-wip`. The upstream web build runs unchanged, and the upstream aiohttp server runs in the browser via Pyodide, so every `/api/*` call is served by the real handler. A GitHub Actions job checks upstream hourly, rebuilds, tests in Chromium, and deploys only when the tests pass. Values stay in your browser (IndexedDB), and outbound network is blocked inside the demo server.
