# Jev Router

[English](README.md)

Claude Code, Codex, Grok에서 턴마다 Jev로 추론 effort를 정해 주는 스킬과, 그 기록을 보는 로컬 대시보드입니다. **모델은 절대 라우팅하지 않습니다.** 모델을 바꾸면 호스트의 프롬프트 캐시가 버려지고, 그 비용이 더 싼 모델로 아끼는 금액보다 큽니다. 그래서 사용자가 고른 모델은 그대로 두고 effort만 바꿉니다.

![License](https://img.shields.io/badge/license-Apache--2.0-blue)
![Runtime](https://img.shields.io/badge/runtime-Python%203.10%2B-66f2c2)

## 저장소 구성

| 경로 | 역할 |
| --- | --- |
| `scripts/jev_effort_hook.py` | `UserPromptSubmit` 훅. 턴이 시작되기 전에 Jev에 이번 턴의 effort를 묻고, 모델에게 얼마나 깊게 작업할지 알려 줍니다. |
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

**훅은 호스트의 effort 설정을 바꾸지 않습니다.** 훅에서 effort 값을 받는 호스트가 없습니다. 작업 깊이 지시는 모든 호스트에서 통하지만, 실제 effort 수치는 출력된 명령을 사용자가 직접 실행해야 바뀝니다: Claude·Grok은 `/effort <level>`, Codex는 `Alt+.` / `Alt+,` 또는 `/model`. Codex에는 스킬 단위 effort 지정 기능이 없고(openai/codex#22908), Claude의 `effort:` frontmatter는 실제로 적용되지 않는다고 보고되어 있습니다(anthropics/claude-code#69267).

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

설정 파일은 `~/.config/jev-router/config.json`이며 `$JEV_ROUTER_HOME`으로 디렉터리를 바꿀 수 있습니다. 사용하는 키는 `efforts`, `judge_context_chars`, `native_fallbacks`입니다.

## 대시보드

```bash
python scripts/jev_dashboard.py --open   # http://127.0.0.1:8787
```

실행마다 다음을 보여 줍니다.

- 난이도와 분류, 선택된 effort와 그 신뢰도·확률 분포, Jev가 골랐는지 사용자 설정인지
- `프롬프트 → 작업/의존성 → effort 결정` 근거 다이어그램
- fallback 여부와 이유, 이어받은 모델, 상태, 소요 시간, 토큰 사용량, 마스킹된 결과 요약
- 새 기록이 생기면 자동 갱신

훅 기록의 effort는 라우팅(또는 fallback)된 값입니다. 사용자가 실제로 `/effort`로 적용했는지는 나타내지 않습니다.

### Provider별 fallback effort

첫 번째 설정 패널에서 Jev가 제시간에 답하지 못했을 때 provider별로 쓸 effort를 정합니다. provider마다 따로 저장되며, **No default — parent handles fallback**을 고르면 해당 값을 지웁니다. 선택지는 `efforts` 목록에서 가져옵니다.

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
- CDN, 분석, 웹폰트, 텔레메트리를 쓰지 않습니다. 디렉터리 탐색을 막도록 run ID와 리포트 경로를 검증하고, 제한적인 CSP·frame·MIME·referrer·cache 헤더를 보냅니다.

실행 기록에는 프롬프트 원문(최대 12,000자)이 디스크에 저장됩니다. `~/.config/jev-router/runs`를 커밋하거나 확인하지 않은 스크린샷을 공개하지 마세요.

## 라이선스

Apache License 2.0. [LICENSE](LICENSE)와 [NOTICE](NOTICE)를 참고하세요.
