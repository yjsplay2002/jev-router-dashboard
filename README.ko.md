# Jev Router

[English](README.md)

Claude Code, Codex, Grok에서 턴마다 Jev로 추론 effort를 정해 주는 스킬과, 그 기록을 보는 로컬 대시보드입니다. **모델은 절대 라우팅하지 않습니다.** 모델을 바꾸면 호스트의 프롬프트 캐시가 버려지고, 그 비용이 더 싼 모델로 아끼는 금액보다 큽니다. 그래서 사용자가 고른 모델은 그대로 두고 effort만 바꿉니다.

![License](https://img.shields.io/badge/license-Apache--2.0-blue)
![Runtime](https://img.shields.io/badge/runtime-Python%203.10%2B-66f2c2)

## 저장소 구성

| 경로 | 역할 |
| --- | --- |
| `scripts/jev_effort_hook.py` | `UserPromptSubmit` 훅. 턴이 시작되기 전에 Jev에 이번 턴의 effort를 묻고, 모델에게 작업 깊이를 알린 뒤 그 값을 프록시에 넘깁니다. |
| `scripts/jev_effort_proxy.py` | 루프백 프록시. 같은 프롬프트의 API 요청에 그 effort를 실어 보냅니다. |
| `scripts/jev_effort.py` | Jev 호출 한 번(1.0초 제한). 훅이 이것을 쓰며, 직접 실행할 수도 있습니다. |
| `SKILL.md` | 에이전트가 따르는 지침: 라우팅된 effort 따르기, 요청이 있을 때만 위임, 근거 기록. |
| `scripts/jev_dashboard.py`, `dashboard/` | 실행 기록과 provider별 fallback effort를 보는 로컬 대시보드. |
| `tests/` | `python -m unittest discover -s tests -v` |

외부 런타임 의존성은 없고 Python 3.10 이상이면 됩니다.

## 턴별 라우팅 동작

1. 프롬프트를 제출하면 호스트가 프롬프트를 stdin으로 넘겨 훅을 실행합니다.
2. 훅은 Jev에 한 번 묻습니다. 제한 시간은 실제 경과 시간 기준 1.0초이며, 작업 스레드를 monotonic 시계로 감싸서 느린 DNS나 멈춘 소켓이 턴을 붙잡지 못합니다.
3. 결정을 보여 주는 한 줄과, 모델에게 작업 깊이를 알려 주는 `additionalContext` 지시를 반환합니다(`low` = 바로 답하기, `high` = 실제 흐름을 추적하고 검증하기).
4. `~/.config/jev-router/runs/<id>/run.json`에 실행 기록을 하나 남깁니다.

```bash
echo '{"prompt":"trace why the migration drops rows"}' | python scripts/jev_effort_hook.py claude
jev: effort=high (jev-1.13.0, 0.39s, conf 99%) - model unchanged -> apply: /effort high

python scripts/jev_effort.py "add a retry guard to the order submit path" --provider codex --json
jev: effort=medium (jev-1.13.0, 0.58s, conf 95%) - model unchanged
```

**Claude와 Codex는 같은 프롬프트에 수치 effort를 실어 보냅니다.** 훅이 effort 값을 직접 받는 호스트는 없습니다. 그래서 훅이 `<router home>/effort/<session id>`에 값을 쓰고, `jev_effort_proxy.py`가 그 세션의 요청이 머신을 떠나기 전에 effort 필드만 바꿉니다. 모델 필드는 건드리지 않습니다. Grok은 여전히 `/effort <level>`을 출력하며, 그 명령을 실행해야 수치가 바뀝니다.

| 호스트 | 요청이 프록시로 가는 방법 | 프록시가 바꾸는 값 |
| --- | --- | --- |
| Claude | `settings.json`의 `ANTHROPIC_BASE_URL=http://127.0.0.1:8791`. SessionStart 훅이 프록시를 띄웁니다. | CLI가 이미 `output_config.effort`를 보냈을 때만 그 값 |
| Codex | `model_provider = "jev"`, `base_url = "http://127.0.0.1:8791/codex"` | CLI가 이미 `reasoning.effort`를 보냈을 때만 그 값 |
| Grok | 요청을 다시 쓰는 경로가 없음 | 훅이 `/effort <level>`을 출력 |

라우팅된 값이 설정의 `efforts` 목록에 있을 때만 요청을 바꿉니다. Jev가 답하지 않으면 훅이 세션 파일을 지우고, CLI가 원래 보내려던 effort가 그대로 통과합니다. 화면에 `applied to this prompt`로 끝나면 이번 프롬프트의 요청에 라우팅된 값이 실렸다는 뜻입니다. Codex의 스킬 단위 effort 지정(openai/codex#22908)과 Claude의 `effort:` frontmatter(anthropics/claude-code#69267)는 이 경로에 필요 없습니다. 프롬프트 캐시 측정 결과(2026-09-23): Claude는 한 세션에서 턴마다 effort를 바꿔도 앞부분 전체를 캐시에서 읽었고 새 턴 분량만 새로 썼습니다. Codex는 최근에 쓰지 않은 effort로 바꾼 첫 턴에서 캐시를 전혀 읽지 못했고, 이전에 썼던 effort로 돌아가면 캐시를 읽었습니다. Codex 쪽 캐시는 effort별로 따로 유지되는 것으로 보이며, 그래서 Codex에서 기어를 자주 바꾸면 캐시를 못 읽는 턴이 생깁니다.

다음 경우 훅은 설정해 둔 effort를 그대로 쓰며, 턴을 막지 않습니다.

- `TYPESAFE_API_KEY`가 없을 때(요청 자체를 보내지 않음)
- Jev 응답이 1.0초를 넘을 때(측정값: 연결이 살아 있으면 0.39–0.48초, TLS 핸드셰이크부터 시작하면 약 1.02초)
- Jev에 연결할 수 없거나 쓸 수 있는 값을 돌려주지 않을 때

`/`로 시작하는 프롬프트와 `JEV_ROUTER_CHILD=1` 환경에서는 훅이 아무것도 하지 않습니다. 어떤 오류든 출력 없이 종료 코드 0으로 끝납니다.

## 외부로 전송되는 데이터

- **훅은 매 턴 제출한 프롬프트 원문을 TypeSafe(`api.typesafe.ai`)로 보냅니다.** `judge_context_chars`(기본 12,000자)까지 잘라서 보내며, 요약이나 마스킹은 하지 않습니다. 프롬프트에 로그, 파일 내용, 키를 붙여넣으면 그대로 전송됩니다.
- 대화 기록, 이전 턴, 첨부 파일, 도구 출력은 보내지 않습니다.
- `jev_effort.py`는 넘겨준 설명을 같은 길이 제한 안에서 그대로 보냅니다.
- 대시보드는 외부 호출을 하지 않습니다.

이것이 곤란한 프로젝트라면 `TYPESAFE_API_KEY`를 설정하지 않거나 훅 항목을 제거하세요.

## 설치

1. 저장소를 스킬 디렉터리에 복사합니다. 예: `~/.claude/skills/jev-router`(Claude), `~/.codex/skills/jev-router`(Codex). Grok 훅은 둘 중 아무 복사본이나 가리키면 됩니다.
2. `~/.config/jev-router/.env`(또는 `$JEV_ROUTER_HOME/.env`)에 `TYPESAFE_API_KEY=...`를 넣습니다.
3. `UserPromptSubmit` 훅을 추가합니다. 마지막 인자는 호스트 이름(`claude`, `codex`, `grok`)입니다.

   | 호스트 | 파일 |
   | --- | --- |
   | Claude | `~/.claude/settings.json` |
   | Codex | `~/.codex/hooks.json` |
   | Grok | `~/.grok/hooks/jev-effort.json` |

   ```json
   {
     "hooks": {
       "UserPromptSubmit": [
         {"hooks": [{"type": "command", "command": "python /path/to/jev-router/scripts/jev_effort_hook.py claude", "timeout": 3}]}
       ]
     }
   }
   ```

   기존 `UserPromptSubmit` 훅이 있으면 덮어쓰지 말고 옆에 추가하세요.

   Windows에서 Codex는 이 명령을 사용자 셸로 실행하는데, `Program Files` 아래의 따옴표로 감싼 `python.exe` 경로는 시작되지 않습니다. Codex 훅은 공백이 없는 경로의 `scripts/jev-effort.cmd`를 가리키세요(`jev-effort.cmd codex`, 첫 요청 전에 프록시를 띄우려면 `jev-effort.cmd codex --session-start`). 명령을 바꾸면 `/hooks`에서 새 명령을 신뢰하기 전까지 Codex가 그 훅을 건너뜁니다.

설정 파일은 `~/.config/jev-router/config.json`이며 `$JEV_ROUTER_HOME`으로 디렉터리를 바꿀 수 있습니다. 사용하는 키는 `efforts`, `judge_context_chars`, `native_fallbacks`입니다. 프록시는 `127.0.0.1:8791`에서 듣습니다(`$JEV_EFFORT_PROXY_PORT`로 포트를 바꿀 수 있으며, 훅과 base URL이 같은 포트를 써야 합니다).

## 대시보드

```bash
python scripts/jev_dashboard.py --open   # http://127.0.0.1:8787
```

대시보드는 기어박스 시프트 게이트 모양입니다. 프롬프트 하나가 기어 변속 한 번이고, 훅이 기어를 *선택*하면 프록시가 실제 요청에 *체결*합니다.

- **게이트 판:** `efforts` 목록으로 그린 H패턴 게이트. 노브가 마지막 프롬프트가 실행된 기어에 꽂혀 있고, 체결 상태, 호스트, 세션, Jev 응답 시간과 확신도, 프롬프트 일부, Jev의 확률 분포를 보여 줍니다.
- **상단 표시등:** `:8791` effort 프록시가 켜져 있는지.
- **기본 기어:** provider별 fallback effort. 그 자리에서 바꿀 수 있습니다.
- **변속 기록:** 턴마다 한 줄이며, 펼치면 프롬프트와 근거가 나옵니다. 예전 위임 실행은 `프롬프트 → 작업 → effort` 근거 흐름을 그대로 보여 줍니다.

턴마다 프록시 요청 로그(`<router home>/effort/applied.log`)를 세션과 시각으로 이어 붙여 다음 중 하나로 표시합니다.

| 표시 | 뜻 |
| --- | --- |
| Engaged(체결) | 프록시가 그 턴의 요청에 라우팅된 값을 넣었습니다(실제로 값을 바꾼 요청 수도 표시). |
| Selected, not engaged(선택만 됨) | Jev가 값을 골랐지만 그 턴의 요청이 프록시를 거치지 않아 호스트 자체 설정으로 실행됐습니다. |
| Fallback | Jev가 제한 시간 안에 답하지 않아 호스트 자체 설정으로 실행됐습니다. |

로그 근거 없이 체결됐다고 추정하지 않습니다. Grok의 턴은 최대 "선택만 됨"입니다.

### Provider별 fallback effort

**Default gear** 패널에서 Jev가 제시간에 답하지 못했을 때 provider별로 쓸 effort를 정합니다. provider마다 따로 저장되며, **Host's own setting**을 고르면 해당 값을 지웁니다. 선택지는 `efforts` 목록에서 가져옵니다.

```json
{"native_fallbacks":{"codex":{"effort":"medium"},"claude":{"effort":null},"grok":{"effort":null}}}
```

`GET /api/config`는 세 항목과 `effort_options`를 돌려줍니다. `PUT /api/config`는 일부 provider만 담은 `native_fallbacks`도 받으며 빠진 provider는 그대로 둡니다. 알 수 없는 provider나 값, 모델을 지정하려는 요청은 아무것도 쓰지 않고 거부합니다. 파일의 다른 설정은 보존됩니다.

옵션:

```text
--runs-dir PATH       다른 실행 기록 디렉터리 읽기
--config PATH         다른 config.json 읽기/수정
--port PORT           다른 localhost 포트 사용
--max-runs N          기록 개수 제한(기본 500)
--include-content     API에 마스킹된 전체 결과 포함
--open                기본 브라우저 열기
```

### 대시보드 보안

- loopback에만 바인딩하고 외부 바인딩은 거부합니다.
- 실행 기록은 읽기 전용입니다. 유일한 쓰기는 `PUT /api/config`(`native_fallbacks`만)이며, same-origin JSON 요청만 받고 파일을 원자적으로 교체합니다.
- 화면에 표시하는 프롬프트와 요약에서 흔한 API 키, bearer 토큰, 비밀번호, GitHub 토큰, 홈 디렉터리 경로를 가립니다.
- CDN, 외부 웹폰트, 분석, 텔레메트리를 쓰지 않습니다. 글꼴 하나(Barlow Condensed, SIL OFL)는 `dashboard/fonts/`에서 제공합니다. 디렉터리 탐색을 막도록 run ID와 리포트 경로를 검증하고, 제한적인 CSP·frame·MIME·referrer·cache 헤더를 보냅니다.

실행 기록에는 프롬프트 원문(최대 12,000자)이 디스크에 저장됩니다. `~/.config/jev-router/runs`를 커밋하거나 확인하지 않은 스크린샷을 공개하지 마세요.

## 라이선스

Apache License 2.0. [LICENSE](LICENSE)와 [NOTICE](NOTICE)를 참고하세요.
