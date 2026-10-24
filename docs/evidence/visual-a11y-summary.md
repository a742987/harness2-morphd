# Visual and accessibility evidence

Generated: 2026-10-24T09:28:35.653Z

Command:

```powershell
npm --prefix consumer/web run build
npm --prefix consumer/web run test:visual:matrix
```

The command starts a temporary local service with a seeded `MORPHD_HOME`, launches
Playwright with the installed Microsoft Edge, writes the screenshots below, and removes
the fixture data afterwards. It fails instead of fabricating screenshots when Edge or the
local service cannot start.

Result: **PASS**

## Environment

- Platform: win32 x64
- Node: v24.12.0
- Edge: `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`
- App version: 0.5.0
- axe-core: 4.14.0
- Local service: http://127.0.0.1:11928/
- Machine-readable report: `docs/evidence/axe-report.json`

## Axe results (all scans)

| State | Serious | Critical | Moderate | Minor | Result |
|---|---:|---:|---:|---:|---|
| onboarding-host-light | 0 | 0 | 0 | 0 | pass |
| onboarding-model-light | 0 | 0 | 0 | 0 | pass |
| onboarding-budget-light | 0 | 0 | 0 | 0 | pass |
| home-light | 0 | 0 | 0 | 0 | pass |
| hosts-light | 0 | 0 | 0 | 0 | pass |
| models-light | 0 | 0 | 0 | 0 | pass |
| learning-light | 0 | 0 | 0 | 0 | pass |
| budget-light | 0 | 0 | 0 | 0 | pass |
| history-light | 0 | 0 | 0 | 0 | pass |
| privacy-light | 0 | 0 | 0 | 0 | pass |
| diagnostics-light | 0 | 0 | 0 | 0 | pass |
| help-light | 0 | 0 | 0 | 0 | pass |
| advanced-light | 0 | 0 | 0 | 0 | pass |
| home-dark | 0 | 0 | 0 | 0 | pass |
| hosts-dark | 0 | 0 | 0 | 0 | pass |
| models-dark | 0 | 0 | 0 | 0 | pass |
| learning-dark | 0 | 0 | 0 | 0 | pass |
| budget-dark | 0 | 0 | 0 | 0 | pass |
| history-dark | 0 | 0 | 0 | 0 | pass |
| privacy-dark | 0 | 0 | 0 | 0 | pass |
| diagnostics-dark | 0 | 0 | 0 | 0 | pass |
| help-dark | 0 | 0 | 0 | 0 | pass |
| advanced-dark | 0 | 0 | 0 | 0 | pass |
| home-125 | 0 | 0 | 0 | 0 | pass |
| home-150 | 0 | 0 | 0 | 0 | pass |
| hosts-125 | 0 | 0 | 0 | 0 | pass |
| hosts-150 | 0 | 0 | 0 | 0 | pass |
| history-125 | 0 | 0 | 0 | 0 | pass |
| history-150 | 0 | 0 | 0 | 0 | pass |
| models-125 | 0 | 0 | 0 | 0 | pass |
| models-150 | 0 | 0 | 0 | 0 | pass |
| home-mobile | 0 | 0 | 0 | 0 | pass |
| home-xl-high | 0 | 0 | 0 | 0 | pass |
| candidate-modal-light | 0 | 0 | 0 | 0 | pass |
| candidate-modal-dark | 0 | 0 | 0 | 0 | pass |

Totals: **0 serious**, **0 critical**, 0 moderate, 0 minor across 35 scans. Serious/critical findings fail the command.

## Screenshots

| File | State | Axe scan |
|---|---|---|
| [onboarding-host-light](ui/onboarding-host-light.png) | Onboarding · find host · blank temporary home | onboarding-host-light |
| [onboarding-model-light](ui/onboarding-model-light.png) | Onboarding · recursive model | onboarding-model-light |
| [onboarding-budget-light](ui/onboarding-budget-light.png) | Onboarding · budget and privacy | onboarding-budget-light |
| [home-light](ui/home-light.png) | home · light · 100% | home-light |
| [hosts-light](ui/hosts-light.png) | hosts · light · 100% | hosts-light |
| [models-light](ui/models-light.png) | models · light · 100% | models-light |
| [learning-light](ui/learning-light.png) | learning · light · 100% | learning-light |
| [budget-light](ui/budget-light.png) | budget · light · 100% | budget-light |
| [history-light](ui/history-light.png) | history · light · 100% | history-light |
| [privacy-light](ui/privacy-light.png) | privacy · light · 100% | privacy-light |
| [diagnostics-light](ui/diagnostics-light.png) | diagnostics · light · 100% | diagnostics-light |
| [help-light](ui/help-light.png) | help · light · 100% | help-light |
| [advanced-light](ui/advanced-light.png) | advanced · light · 100% | advanced-light |
| [home-dark](ui/home-dark.png) | home · dark · 100% | home-dark |
| [hosts-dark](ui/hosts-dark.png) | hosts · dark · 100% | hosts-dark |
| [models-dark](ui/models-dark.png) | models · dark · 100% | models-dark |
| [learning-dark](ui/learning-dark.png) | learning · dark · 100% | learning-dark |
| [budget-dark](ui/budget-dark.png) | budget · dark · 100% | budget-dark |
| [history-dark](ui/history-dark.png) | history · dark · 100% | history-dark |
| [privacy-dark](ui/privacy-dark.png) | privacy · dark · 100% | privacy-dark |
| [diagnostics-dark](ui/diagnostics-dark.png) | diagnostics · dark · 100% | diagnostics-dark |
| [help-dark](ui/help-dark.png) | help · dark · 100% | help-dark |
| [advanced-dark](ui/advanced-dark.png) | advanced · dark · 100% | advanced-dark |
| [home-125](ui/home-125.png) | home · light · 125% DPI | home-125 |
| [home-150](ui/home-150.png) | home · light · 150% DPI | home-150 |
| [hosts-125](ui/hosts-125.png) | hosts · light · 125% DPI | hosts-125 |
| [hosts-150](ui/hosts-150.png) | hosts · light · 150% DPI | hosts-150 |
| [history-125](ui/history-125.png) | history · light · 125% DPI | history-125 |
| [history-150](ui/history-150.png) | history · light · 150% DPI | history-150 |
| [models-125](ui/models-125.png) | models · light · 125% DPI | models-125 |
| [models-150](ui/models-150.png) | models · light · 150% DPI | models-150 |
| [home-mobile](ui/home-mobile.png) | home · light · mobile 375px | home-mobile |
| [home-xl-high](ui/home-xl-high.png) | home · light · xlarge font + high contrast | home-xl-high |
| [candidate-modal-light](ui/candidate-modal-light.png) | candidate modal · light | candidate-modal-light |
| [candidate-modal-dark](ui/candidate-modal-dark.png) | candidate modal · dark | candidate-modal-dark |
| [keyboard-nav-focus](ui/keyboard-nav-focus.png) | keyboard · focused primary navigation | — |
| [candidate-modal-keyboard](ui/candidate-modal-keyboard.png) | keyboard · candidate modal opened with Enter | — |
| [keyboard-revert-confirm](ui/keyboard-revert-confirm.png) | keyboard · revert confirmation before actual restore | — |

## Keyboard-only checks

| Check | Result | Detail | Evidence |
|---|---|---|---|
| Skip link is the first Tab stop with a visible ring | pass | 跳到主要内容; outline 2px; shadow rgb(118, 185, 0) 0px 0px 0px 2px | — |
| All 10 primary navigation items are Tab-reachable | pass | 10/10 reached: 首页 → 宿主 → 模型 → 学习 → 预算 → 历史1 → 隐私 → 诊断 → 帮助 → 高级 | — |
| Primary navigation shows a visible focus ring on every item | pass | 10/10 items with outline+offset shadow; first: 首页 | docs/evidence/ui/keyboard-nav-focus.png |
| Enter activates the focused navigation item | pass | Focused 宿主/Hosts, then Enter; title is hosts · morphd | — |
| Candidate detail action is Tab-reachable with a visible ring | pass | 查看改动与原因; 22 Tab stops; outline 2px; shadow rgb(118, 185, 0) 0px 0px 0px 2px | — |
| Candidate modal opens by keyboard and receives focus | pass | focus is inside role=dialog | docs/evidence/ui/candidate-modal-keyboard.png |
| Tab focus remains inside the candidate modal | pass | 14 Tab presses stayed inside the dialog | — |
| Escape closes the modal and returns focus to its trigger | pass | focus restored to: 查看改动与原因 | — |
| Feedback button can be activated by keyboard and is recorded | pass | 有用; feedback reasons: useful | — |
| Pause can be triggered by keyboard | pass | focused 暂停 1 天; /api/status reports learning.paused=true | — |
| Resume can be triggered by keyboard | pass | focused 恢复学习; /api/status reports learning.paused=false | — |
| Incognito can be enabled and cancelled by keyboard | pass | focused 下一次会话隐身, then 取消下一次隐身 | — |
| Revert confirmation opens by keyboard and restores the snapshot | pass | card 4, revert 12; state=reverted; file restored=true | docs/evidence/ui/keyboard-revert-confirm.png |

Keyboard checks use Tab/Shift+Tab and Enter/Escape only. Focus rings are measured
from computed styles (outline + green offset shadow), never from color names.

## Console and page errors

None.

## Evidence notes

- Fixture records follow the versioned JSON layouts used by `morphd/consumer` and its tests.
- The seeded home includes one adapted dsh host, one provider, three sessions, budget journal
  entries, a needs-review candidate, and a trial candidate with a real snapshot for revert.
- Onboarding is captured against a second, blank temporary home so the first-run flow is real.
- Temporary fixture data and the local service are removed/stopped when the command exits.
