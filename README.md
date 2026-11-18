# CUBRID GHA monitor

CUBRID CI의 실제 작업 진행 상황과 러너 풀별 대기열을 로컬 브라우저에서 확인합니다. 시작 및 완료 시각은 조건부 추정입니다.

## 실행

Python 3.11 이상과 인증된 GitHub CLI가 필요합니다. 브라우저에는 인증 정보가 전달되지 않습니다.

```sh
just serve
```

출력된 `http://127.0.0.1:8765`를 엽니다. 처음에는 GitHub에서 실행과 작업 상세를 수집합니다. 재시작하면 SQLite의 최근 관측값을 바로 표시하고, 백그라운드에서 GitHub 실행 목록을 확인합니다. 진행 중인 실행과 새 시도의 작업 상세를 가져오고, 같은 완료 시도의 상세는 재사용합니다. 대시보드를 열어두면 60초마다 갱신합니다.

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

세 시작 명령은 현재 사용자의 `cubrid-gha-monitor.service`를 공유합니다. 실행 중에 시작 명령을 다시 실행하면 기존 인스턴스의 URL을 표시합니다. 시작 옵션을 바꾸려면 `just down`으로 종료한 뒤 `just up --port 9000`처럼 다시 시작합니다.

서비스 상세 상태는 `systemctl --user status cubrid-gha-monitor.service`로 확인합니다. 로그는 `journalctl --user -u cubrid-gha-monitor.service`로 확인합니다. 재부팅 후에는 시작 명령을 직접 실행합니다.

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
