# Claude Code 에이전트 팀

리뷰·진단용 서브에이전트 9종을 로컬 설치용으로 묶은 패키지입니다.

[`surplus96/Langgraph-MCP-Agent`](https://github.com/surplus96/Langgraph-MCP-Agent)
저장소의 `.claude/agents/`에서 추출했습니다. `agents/` 안의 정의 파일은 그
저장소가 실제로 쓰는 것과 **바이트 단위로 동일**합니다 — `MANIFEST.txt`로 확인
가능합니다.

영문: [README.md](README.md)

## 구성

```
agents/            에이전트 정의 9개 + 라우팅 README
install.ps1        설치 스크립트 (Windows PowerShell)
install.sh         설치 스크립트 (Linux / macOS)
snippets/          다른 프로젝트의 CLAUDE.md에 붙여넣을 블록
MANIFEST.txt       각 파일의 sha256
```

## 9종

| 에이전트 | 분류 | 답하는 질문 |
|---|---|---|
| `code-quality-reviewer` | 감사 | 유지보수 가능한가? |
| `security-reviewer` | 감사 | 공격 가능한가? |
| `pipeline-optimizer` | 감사 | LLM 런타임이 최신이고 토큰 효율적인가? |
| `docs-reviewer` | 감사 | 문서가 코드와 일치하는가? |
| `interface-reviewer` | 감사 | 화면이 사용자에게 사실을 말하는가? |
| `debugger` | 진단 | 왜 실패하는가? |
| `build-doctor` | 진단 | 왜 빌드·설치·CI 실행이 안 되는가? |
| `performance-profiler` | 진단 | 시간은 어디로 가는가? |
| `test-engineer` | 검증 | 코드가 틀렸다면 이 테스트가 잡아내는가? |

라우팅 규칙 전문, 각 에이전트가 생겨난 계기, **의도적으로 만들지 않은 역할**
목록은 [`agents/README.md`](agents/README.md)에 있습니다. 열 번째를 추가하기
전에 그 문서를 먼저 읽으십시오.

## 설치

범위는 둘이고, 배타적이지 않습니다. 프로젝트 사본이 사용자 사본을 가립니다.

| 범위 | 대상 경로 | 언제 |
|---|---|---|
| **user** | `%USERPROFILE%\.claude\agents\` (Unix는 `~/.claude/agents/`) | 이 머신의 모든 프로젝트에서 쓰고 싶을 때 |
| **project** | `<프로젝트>/.claude/agents/` | 특정 저장소에 함께 커밋해서 협업자·CI 세션도 받게 할 때 |

### Windows (PowerShell)

```powershell
# 이 머신의 모든 프로젝트
.\install.ps1 -Scope user

# 특정 저장소
.\install.ps1 -Scope project -Path D:\cty_ai\some-project

# 무엇이 바뀌는지만 보고 아무것도 쓰지 않음
.\install.ps1 -Scope user -DryRun
```

PowerShell이 서명되지 않은 스크립트 실행을 거부할 수 있습니다. 이 파일만
해제하거나

```powershell
Unblock-File .\install.ps1
```

해당 세션에서만 우회하십시오.

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Scope user
```

> CMD(`cmd.exe`)에는 `cp`가 없습니다. 위 명령은 PowerShell에서 실행하십시오.
> CMD에서 수동 복사만 하려면: `xcopy /Y agents\*.md "%USERPROFILE%\.claude\agents\"`
> (대상 폴더가 없으면 먼저 `mkdir "%USERPROFILE%\.claude\agents"`)

### Linux / macOS

```bash
chmod +x install.sh
./install.sh --scope user
./install.sh --scope project --path ~/code/some-project
./install.sh --scope user --dry-run
```

### 수동 설치

`agents/*.md`를 `.claude/agents/`에 복사하면 끝입니다. 이게 메커니즘의 전부입니다
— Claude Code가 그 디렉터리의 모든 `.md`를 읽어 프론트매터의 `name`을 등록합니다.
새 세션을 시작하는 것 외에 재시작할 것은 없습니다.

두 설치 스크립트 모두 `-Force`(`--force`) 없이는 기존 파일을 덮어쓰지 않으며,
건너뛴 파일 이름을 전부 출력합니다.

## 설치 확인

대상 프로젝트에서 Claude Code 세션을 새로 열고 이름을 직접 불러보십시오.

```
use the security-reviewer agent to audit this repository
```

에이전트를 못 찾으면 파일이 `.claude/`가 아니라 `.claude/agents/`에 들어갔는지,
프론트매터 첫 키가 여전히 `name:`인지 확인하십시오.

## 다른 스택의 프로젝트로 옮길 때

대부분 스택 중립이지만, 세 파일에 출신 프로젝트의 흔적이 남아 있습니다. 그대로
써도 동작하며, 다만 없는 대상에 몇 문장을 쓸 뿐입니다.

| 파일 | 위치 | 언급 |
|---|---|---|
| `code-quality-reviewer.md` | 프론트매터, 항목 4 | Python / Streamlit `session_state` |
| `pipeline-optimizer.md` | 프론트매터, 항목 3·7 | LangGraph 배선, Streamlit rerun |
| `security-reviewer.md` | 항목 5 | Streamlit CORS/XSRF, MCP 신뢰 경계 |

`agents/README.md`의 라우팅 표에도 LangGraph가 한 번 나옵니다.

LLM 애플리케이션이 아닌 프로젝트라면 할 일이 없어지는 건 `pipeline-optimizer`
하나입니다. 고쳐 쓰기보다 빼십시오. 나머지는 그대로 이식됩니다.

## 이걸 설치할 가치가 있는 이유

출처 저장소의 `CLAUDE.md`에서:

> **녹색 테스트 스위트는 증거가 아니다. 코드를 깨뜨렸을 때 테스트가 깨져야만
> 그 테스트가 유효하다.**

그리고

> 자기 작업을, 그것을 만들어낸 바로 그 컨텍스트 안에서 리뷰하는 것 — 캐싱
> no-op, 살아남은 뮤테이션, 절반만 적용된 타임아웃 수정이 전부 처음에 그렇게
> 빠져나갔다.

9종 각각은 셀프 리뷰를 통과해버린 **구체적인 결함** 때문에 존재합니다. 어떤 결함이
어떤 에이전트를 만들었는지는 `agents/README.md`에 적혀 있습니다. 설치해두고
호출하지 않으면 원래의 문제가 그대로 재현됩니다.

## 라이선스

출처 저장소와 동일한 MIT.
