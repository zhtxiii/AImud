# Long Runtime Observation - 2026-05-30

Scope: run the agent for a long observation window against the local MUD on `127.0.0.1:4000`, record issues without changing code.

Branch: `fix/runtime-control-state`

Start time: 2026-05-30 17:31:54 CST

Pre-run state:
- No existing `agent.py` process was running.
- Local MUD was reachable on `127.0.0.1:4000`.
- Worktree had one pre-existing unstaged runtime-data change: `data/reflections/experiences.json`.

## Timeline

- 17:31:54 - Prepared long test report.
- 17:32:05 - Started foreground run with `timeout 900s python3 -u agent.py`.
- 17:32:05 - Agent loaded 18 experiences and 9 skills, then connected to the MUD and entered phase 1.
- 17:32:12 - P1-T1 observed the MUD welcome screen and login prompt.
- 17:32:15 - P1-T1 completed without sending an extra action. This confirms the new analyze routing prevented post-completion actions.
- 17:32:36 - P1-T2 sent `look` while still at the login prompt. The server treated it as a username and prompted for password.
- 17:32:40 - P1-T2 sent `<test-password>`; the server returned `密码错误！`.
- 17:32:43 - P1-T2 sent `newplayer` while still in the failed login/password flow; the server closed the connection.
- 17:32:49 - Agent reconnected automatically and resumed P1-T2.
- 17:33:02 - P1-T2 sent `newplayer` at the fresh login prompt.
- 17:33:06 - P1-T2 completed once the server showed new-character confirmation.
- 17:33:25 - Planner entered phase 2: `角色创建`.
- 17:33:39 - P2-T1 sent `n` to cancel the pending `newplayer` creation prompt and restart.
- 17:33:42 - P2-T1 sent invalid name `newtest007`; server rejected digits.
- 17:33:45 - P2-T1 sent existing name `testplayer`; server prompted for password.
- 17:33:49 - P2-T1 used action `enter` at a password prompt; server treated it as an empty password and returned `密码错误！`.
- 17:33:52 - P2-T1 sent `westplay` while still in failed password flow; server closed connection.
- 17:34:09 - After reconnect, P2-T1 sent overlong name `testplayerabc`; server rejected length.
- 17:34:14 - P2-T1 sent valid new name `newtest`.
- 17:34:17 - Confirmed creation with `y`.
- 17:34:21 to 17:34:30 - Filled creation fields: Chinese name `<test-cn-name>`, password `<test-password>` twice, email `<test-email@example.invalid>`.
- 17:34:33 - Selected male gender with `m`; character creation completed and entered the game.
- 17:34:36 - P2-T1 completed without a stray post-completion payload.
- 17:34:40 to 17:34:47 - Knowledge manager consolidated phase 2 knowledge from 30 entries to 14.
- 17:35:10 - P2-T2 completed from the login room description without sending `look`, even though the task description explicitly said to use `look`.
- 17:35:32 - P2-T3 sent `look` at the inn.
- 17:35:34 - P2-T3 moved `east` to `广场`.
- 17:35:37 - P2-T3 moved `south` to `雪亭镇街道`.
- 17:35:40 - P2-T3 completed after recording three rooms, with no extra action after completion.
- 17:36:07 - P2-T4 completed by summarizing prior creation errors and success. No extra action was sent.
- 17:36:21 - Planner entered phase 3: `初始探索`.
- 17:36:35 - P3-T1 sent `help`.
- 17:36:38 - P3-T1 sent `help cmds`.
- 17:36:40 - Server returned command categories and ended with pager prompt `== 未完继续 95% == (ENTER 继续下一页，q 离开，b 前一页)`.
- 17:36:43 - P3-T1 marked complete despite the pager prompt, claiming a complete command list.
- 17:37:03 - P3-T2 sent `score` while the server was still at the previous pager prompt.
- 17:37:05 - Server returned the pager prompt again.
- 17:37:07 - P3-T2 selected `enter`; the agent sent `<ENTER>` and the server returned the remaining pager content plus normal prompt.
- 17:37:10 - P3-T2 retried `score` and received the actual score output.
- 17:37:14 - P3-T2 sent `hp`.
- 17:37:17 - P3-T2 sent `inventory`.
- 17:37:20 - P3-T2 completed successfully after collecting score/hp/inventory.
- 17:37:52 - P3-T3 attempted `ask waiter about sign`; server replied `这里没有这个人。`
- 17:38:02 - P3-T3 attempted `look sign`; server replied `你要看什麽？`
- 17:38:06 - P3-T3 attempted `ask waiter sign`; server replied `这里没有这个人。`
- 17:38:09 - P3-T3 attempted `id waiter`; server replied `什么？`
- 17:37:52 - Knowledge manager had one JSON parse failure and retried.
- 17:38:20 - P3-T3 ran `look`, discovered the current location was `雪亭镇街道`, not the inn.
- 17:38:21 to 17:38:28 - P3-T3 navigated `north` to `广场`, then `west` back to `饮风客栈`.
- 17:38:30 - P3-T3 used `list` successfully at the inn.
- 17:38:33 - P3-T3 used `get coin` successfully.
- 17:38:37 - P3-T3 tried `give coin to waiter`; server replied that items can only be given to player-controlled characters.
- 17:38:42 - P3-T3 marked completed with a partial-success summary.
- 17:39:10 to 17:39:22 - P3-T4 explored from inn -> square -> street -> 黄土路 -> 卧龙岗北坡 and completed.
- 17:39:36 - Planner entered phase 4: `任务与交互`.
- 17:39:52 - P4-T1 moved `southup` to `卧龙岗`, encountering two `Gangster` NPCs.
- 17:39:55 - P4-T1 moved `southdown`; server indicated gangsters wanted to kill the player and combat text appeared.
- 17:39:58 - Despite active attack/combat text, P4-T1 sent `look`.
- 17:40:03 - P4-T1 continued to `southeast` and reached `绮云镇`.
- 17:40:06 - P4-T1 completed, recording gangsters as combat NPCs.
- 17:40:34 - P4-T2 started from `绮云镇` and moved `northwest`, reaching `黄土路`.
- 17:40:38 - From `黄土路`, where exits were `southeast` and `northwest`, P4-T2 sent invalid direction `north`.
- 17:40:40 - Server returned `什么？`; P4-T2 then incorrectly reasoned it was at `雪亭镇街道`.
- 17:40:41 - P4-T2 sent `look traveller`, which failed.
- 17:40:45 - P4-T2 sent `north` again from the wrong assumed location.
- 17:40:56 - P4-T2 hallucinated current location as `广场` after another `什么？`, then sent `ask traveller about 方向`; server replied no such person.
- 17:41:12 - P4-T2 sent `southeast`, moving to `绮云镇`, away from the traveller objective.
- 17:41:15 - P4-T2 inspected `look sign` at `绮云镇`, unrelated to traveller interaction.
- 17:41:19 - P4-T2 moved `northwest` back to `黄土路`.
- 17:41:44 - Analyze had one JSON parse/control-character failure and retried.
- 17:41:44 - P4-T2 sent `northwest` from `黄土路`, reaching `卧龙岗南坡`.
- 17:41:51 - P4-T2 moved `northup` into `卧龙岗`, triggering hostile gangsters again.
- 17:41:56 - P4-T2 moved `northdown` while combat text continued, reaching `卧龙岗北坡`.
- 17:42:00 - P4-T2 moved `northwest` to `黄土路`.
- 17:42:05 - P4-T2 sent `north` from `黄土路`.
- 17:42:05 to 17:44:06 - No stdout for roughly two minutes, likely waiting on knowledge sync or an LLM call.
- 17:44:06 - `sync_kb` logged `后台知识更新失败:` with no exception message.
- 17:44:06 - Server output showed the character at `雪亭镇街道`.
- 17:44:08 - P4-T2 sent `north` to reach a room with travellers.
- 17:44:08 to 17:46:09 - Another roughly two-minute pause occurred.
- 17:46:09 - `sync_kb` again logged `后台知识更新失败:` with no details.
- 17:46:09 - Server output showed the character at `广场` with three `Traveller` NPCs.
- 17:46:11 - P4-T2 sent `ask Traveller about 方向`, changing only capitalization from the previous failed `traveller`.
- 17:47:05 - The 900 second timeout ended before a response to `ask Traveller about 方向` was observed.
- 17:47:25 - Confirmed no `agent.py` process remained.

## Issues

- Existing reflection data affects the run. The agent starts with 18 experiences and 9 skills, so this is not a clean-slate behavior test.
- The control-flow fix is effective for completed tasks: P1-T1 completed and did not send a stray payload afterward.
- P1-T2 still performs invasive probing during environment identification. It sent `look` at a login prompt, which became a username, then guessed password `<test-password>`.
- The agent still lacks a login-mode safety policy. It did not distinguish "environment identification" from "attempt account login/create"; this caused a password error and reconnect.
- The new `enter` action works mechanically, but action choice remains unsafe. At 17:33:49 it sent ENTER at a password prompt, which was interpreted as an empty password and caused another password error.
- Character creation is unconstrained: the agent uses weak password `<test-password>`, placeholder email `<test-email@example.invalid>`, and generic Chinese name `<test-cn-name>` without an explicit policy for safe test credentials.
- The agent learns constraints by trial and error, but slowly: it tried digits in an English name, then an overlong name, before satisfying the 3-12 letter rule.
- The post-completion action bug appears fixed in normal cases: P2-T1 and P2-T3 completed without sending extra payloads.
- The agent may satisfy a task from equivalent observed data instead of executing the literal requested command. P2-T2 completed from prior room output without issuing `look`, even though the task explicitly requested `look`.
- Movement exploration is substantially more stable after the control-flow/history changes: P2-T3 moved through rooms, recorded descriptions, and stopped after meeting the goal.
- Pager state is still not explicitly tracked. P3-T1 completed while the server was still in a pager prompt after `help cmds`.
- The agent overstates completeness around paginated output. It described the `help cmds` result as complete even though the server showed there was remaining content.
- The new `enter` action works in practice. P3-T2 used `<ENTER>` to clear the lingering pager prompt and then continued with `score`.
- Current-location tracking is weak. P3-T3 believed it was in `饮风客栈`, but the last movement left the character at `雪亭镇街道`; interactions with `waiter` and `sign` failed because those objects were not present.
- The agent does not consistently run `look` before object-specific interaction, even when the plan says to confirm location first.
- Knowledge manager JSON output is still occasionally invalid; retry handled one parse error during P3-T3.
- Recovery from location mistakes improved: after seeing `look` output, P3-T3 correctly navigated back to the inn.
- Partial failures can still be marked `completed` by `analyze`. P3-T3 completed despite failed `ask`, failed `look sign`, failed `id waiter`, and failed `give`; the result summary says "partial", but task status is completed.
- Direction summarization can be imprecise. P3-T4 moved `southeast`, but the completion summary described the route as moving "向南" toward `卧龙岗北坡`.
- Combat/hostile-state awareness is insufficient. P4-T1 encountered hostile gangsters and combat text, but continued exploration instead of explicitly assessing fight/escape/safety state.
- Map and location tracking remain fragile. P4-T2 ignored the current room's listed exits (`southeast`, `northwest`) and chose invalid `north`; after the error, it hallucinated being at `雪亭镇街道`.
- The agent still sends object commands without confirming local object presence. `look traveller` was attempted after a failed movement, in a location that did not show travellers.
- P4-T2 shows goal drift. After failing traveller interaction, it detoured to `绮云镇` and inspected a sign, which did not advance the traveller task.
- Navigation recovery can route through dangerous areas unnecessarily. P4-T2 returned through `卧龙岗`, triggering hostile gangsters again.
- Analyze JSON output can also be invalid, not just knowledge manager output; one control-character parse failure occurred at 17:41:44 and was retried.
- Knowledge synchronization can block visible progress for minutes. A roughly two-minute pause ended with a generic `后台知识更新失败:` message.
- Knowledge update failures are poorly observable: the log line did not include the exception type or message.
- The knowledge-update blocking/failure pattern repeated twice in the same task.
- NPC id recovery remains shallow. After `ask traveller ...` failed, the next hypothesis was only capitalization (`Traveller`) rather than using a command like `id here` or consulting command help.

## End State

- Run duration: 900 seconds.
- Final observed task: phase 4, P4-T2.
- Final observed action: `ask Traveller about 方向`.
- Final observed location before timeout: `广场`.
- Process status after timeout: no `agent.py` process running.
- Files changed by the test: runtime logs/data under `logs/` and `data/`, plus this report file.
