# Demo script (about five minutes)

Two terminals, and a browser tab ready for `runs/<id>/evidence/card.html`.

The automated path, if you do not want to narrate a live stop, is `ksdrill drill`. The script below is the live version. `--pause-at-step 3` keeps the first confirmation email in flight while you talk; the mock scenario would otherwise finish in a few seconds. `ksdrill harness up` leaves the token-layer probe on, and a queued email waits 60 seconds before it is sent, so a narrated stop still sees it held. Override that wait with `ksdrill agent start --pause-at-step 3 --email-delay-ms 120000`. After the bypasses, `ksdrill card <run-id>` builds the page from the live stop.

| Time | What you say | What you run |
| --- | --- | --- |
| 0:00–0:30 | "This autumn two authorities asked the same question: can you actually stop an AI agent? The HKMA discussion paper of 2 November lists six things a stop should do. ANSSI asked for an emergency stop on 30 October, and the EU AI Act already speaks of a 'stop' button for high-risk systems. I'll show you a drill that tests all six on a live agent." | README header |
| 0:30–1:15 | "This is a small agent paying three supplier invoices and emailing confirmations. Fake tools, fake bank. It holds one token per tool, each short-lived and scoped to one action." | Terminal A: `ksdrill harness up` then `ksdrill agent start --pause-at-step 3`. Terminal B: `ksdrill status --watch` (jti only, step counter). |
| 1:15–1:45 | "It has paid one invoice. An email is waiting to go. Now I press stop." | Terminal B: `ksdrill stop --operator joe --reason "demo: threshold breach"` |
| 1:45–2:30 | "One command, six steps, each timed: task halted, credentials revoked, tool connections cut, outbound paused, fallback on, logs sealed. The total is right here, in milliseconds. Those are laptop numbers, not a promise about your bank. The two-second line is our demo target. The paper does not give a number." | Step table with milliseconds and PASS |
| 2:30–3:45 | "Now let's try to cheat. Reuse the old token: rejected. Forge one: rejected. Ask for a new one: broker locked. Send the email: paused, still held. Restart the agent: refused. Restart the whole harness: it comes back stopped. Edit the log: the hash chain catches it." | `ksdrill bypass`, or show the bypass table from `ksdrill drill` run just before |
| 3:45–4:30 | "And this is what you'd hand an auditor: one page, every line mapped to a clause in the HKMA paper, the ANSSI essentials, or the EU AI Act articles on human oversight and logging. The map was checked against the texts, including Regulation (EU) 2026/1744." | `ksdrill card <run-id>`, then the browser on `card.html` and the PDF |
| 4:30–5:00 | "This is a demo agent. The real question is whether your stop works on your agent. That's the drill we run on your staging environment: fixed scope, same card." | Card footer, the disclaimer |

If the live run misbehaves, `ksdrill card <last-good-run>` rebuilds the card from that run. It is labelled with that run's time.

The disclaimer on the card, and in the README, is fixed text:

> Synthetic demo agent and fake tools. Not an HKMA, ANSSI or EU assessment or certification. HKMA DP is non-binding (§2.3 p. 11). EU AI Act articles cited apply to high-risk AI systems. Timings measured on this machine.
