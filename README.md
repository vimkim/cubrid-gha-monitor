# CUBRID GHA monitor

CUBRID CI의 실제 작업 진행 상황과 러너 풀별 대기열을 로컬 브라우저에서 확인합니다. 시작 및 완료 시각은 조건부 추정입니다.

## 실행

Python 3.11 이상과 인증된 GitHub CLI가 필요합니다. 브라우저에는 인증 정보가 전달되지 않습니다.

```sh
just serve
```

출력된 `http://127.0.0.1:8765`를 엽니다. 처음에는 GitHub에서 실행과 작업 상세를 수집합니다. 재시작하면 SQLite의 최근 관측값을 바로 표시하고, 백그라운드 폴링 스레드가 활성 실행을 먼저 갱신한 뒤 완료 이력을 주기적으로 갱신합니다. 같은 완료 시도의 상세는 재사용하고 스킵된 gate-only 완료 실행은 건너뜁니다. 대시보드를 열어두지 않아도 백그라운드에서 활성 실행(45초)과 유휴 상태(60초) 주기로 최신 스냅샷을 유지합니다.

러너 풀 카드(`cubrid-arc-build`, `cubrid-arc`, `cubrid-arc-light`)에서는 풀별 사용률, 워크로드 유형별(`build (release/debug)`, `shell`/`sql`/`sql_by_cci`, `plan`/`medium`/`collect`/`rerun`) 실행·대기 수와 점유·대기 중인 실행 내역을 확인할 수 있습니다. 각 유형을 클릭하면 활성 실행 목록을 해당 작업으로 필터링합니다.

캐시 위치는 `~/.cache/cubrid-gha-monitor/observations.sqlite3`입니다. `XDG_CACHE_HOME`을 설정하면 그 디렉터리에 저장합니다. 종료하려면 `Ctrl+C`를 누릅니다. 원격 서버에서는 로컬 컴퓨터에서 `ssh -L 8765:127.0.0.1:8765 <server>`로 포트를 전달합니다.

모든 IPv4 인터페이스에서 접속을 받으려면 `just serve-for-all`을 실행하고, 브라우저에서 `http://<서버-IP>:8765`를 엽니다.

Dev2의 `192.168.4.2`에서 접속을 받으려면 `just serve-dev2`를 실행하고, `http://192.168.4.2:8765`를 엽니다.

GitHub 없이 예시를 보려면 `just demo`를 실행합니다. 예시 화면에는 DEMO가 표시됩니다. 포트나 풀 용량을 바꾸려면 `python3 -m monitor.server --help`를 확인합니다. 기본 용량은 build 8, general 174, light 20이며 2026-11-17에 확인한 설정값입니다. 실제 가동 중인 러너 수가 아닙니다. `0`은 용량 미확인으로 취급합니다.

### 백그라운드 실행

Linux에서 systemd 사용자 서비스 관리자가 실행 중이면 `just up`으로 대시보드를 백그라운드에서 시작합니다.

- `just up`은 기본 주소 `http://127.0.0.1:8765`에서 접속을 받습니다.
- `just up-dev2`는 `http://192.168.4.2:8765`에서 접속을 받습니다.
- `just up-for-all`은 모든 IPv4 인터페이스에서 접속을 받습니다.

시작 명령은 대시보드가 포트를 열 때까지 기다린 뒤 접속 URL을 표시합니다. 나중에 실행 상태와 URL을 확인하려면 `just status`를 실행합니다. 모든 IPv4 인터페이스에서 접속을 받는 경우 로컬 URL과 확인 가능한 인터페이스의 URL을 표시합니다.

세 시작 명령은 현재 사용자의 `cubrid-gha-monitor.service`를 공유합니다. 영구 유닛(`just install`) 설치 후 `just up-dev2`나 `just up-for-all`, 또는 `just up --host ... --port ... --build-capacity ...`를 실행하면 `service.env`를 갱신하고 서비스를 재시작합니다. 재시작만 하려면 `just restart`를 실행합니다. 임시 실행(`just install` 미설치) 중 시작 옵션을 바꾸려면 `just down`으로 종료한 뒤 `just up --port 9000`처럼 다시 시작합니다.

서비스 상세 상태는 `systemctl --user status cubrid-gha-monitor.service`로 확인합니다. 최근 로그는 `just logs`로 확인합니다.

### 영구 사용자 서비스

유지할 체크아웃에서 `just install`을 실행하면 현재 경로와 PATH를 사용하는
사용자 유닛이 설치됩니다. 실행 중인 임시 서비스는 계속 동작합니다.
전환할 때는 사용이 끝난 뒤 `just down`을 실행하고 `just up`(또는 `just up-dev2` / `just up-for-all`)으로 설치된 유닛을 시작합니다.

설치된 유닛은 기본적으로 `127.0.0.1:8765`에 바인딩합니다. 주소, 포트, 풀 용량을
수동으로 바꾸려면 `${XDG_CONFIG_HOME:-$HOME/.config}/cubrid-gha-monitor/service.env`에
`MONITOR_HOST`, `MONITOR_PORT`, `MONITOR_BUILD_CAPACITY`, `MONITOR_GENERAL_CAPACITY`, `MONITOR_LIGHT_CAPACITY`를 `NAME=value` 형식으로 설정한 뒤 `just restart`를 실행합니다.
필요하면 `GH_CONFIG_DIR`이나 `XDG_CACHE_HOME`도 이 파일에 설정합니다.
인증에는 서비스 사용자 계정의 GitHub CLI 설정을 사용합니다.

부팅 시 시작하려면 `systemctl --user enable cubrid-gha-monitor.service`를 실행합니다.
로그인 전에 실행하려면 해당 사용자 계정의 systemd linger가 필요합니다.

## 확인

`just test`는 표준 라이브러리 단위 테스트를 실행합니다. `just smoke`는 별도로 설치된 Playwright와 Chromium을 사용해 데모를 헤드리스로 확인합니다. 다른 설치 경로는 `PLAYWRIGHT_MODULE=/absolute/path/to/playwright just smoke`로 지정합니다.

선택적으로 프로젝트 디렉터리에 테스트 도구를 설치할 수 있습니다. 애플리케이션 실행에는 필요하지 않습니다.

```sh
npm install --no-save --no-package-lock playwright
npx playwright install chromium
just smoke
```

## 해석

대기 순서는 관측된 준비 작업의 생성 시각 순서로 계산한 모델입니다. GitHub가 같은 순서로 작업을 배정한다는 보장은 없습니다. 일반 풀에 설정상 빈 자리가 있어도 오래 기다리는 작업이 있으면 정체로 표시하고 예상을 중지합니다. 이력 부족, 실행 시간 초과, 수집 실패, 미확인 풀 또는 작업 상태도 예상을 제한합니다.

최근 완료 실행은 최대 100개를 조사합니다. 보이는 작업의 시작 예상과 전체 CI 완료 시나리오는 다릅니다. 전체 완료 시나리오는 완료된 동일 작업 구성을 세 번 이상 관측한 경우에만 표시합니다. 이후 다른 작업이 생성되거나 러너 상황이 바뀌면 이 시나리오는 맞지 않을 수 있습니다. 세부 기준은 [architecture](docs/architecture.md)를 참조하세요.
