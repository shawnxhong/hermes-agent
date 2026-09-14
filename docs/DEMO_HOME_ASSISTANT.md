# Local simulated home

Independent stdlib HTTP service + native Hermes plugin + scenario skill.
No general harness, existing tool schemas, voice I/O, model, or email defaults
are modified. No real appliances are connected. English and Chinese supported.

Install `scripts/local-ovms/plugins/demo-home/` into `$HERMES_HOME/plugins/`,
and `scripts/local-ovms/skills/demo-home-assistant/` into
`$HERMES_HOME/skills/productivity/`. Enable `demo-home` in `plugins.enabled`
and the `demo_home` toolset for CLI and desired IM platforms. Start a new
conversation to load the plugin's fixed scenario-discovery hint.

For the default Linux Box profile, install `hermes-demo-home.service` into
the user's systemd directory, then `systemctl --user daemon-reload` and
`systemctl --user enable --now hermes-demo-home.service`. User lingering is
required for headless boot. This does not enable the separate voice service.

Service listens only on `127.0.0.1:8769`; no external access or proxy. Tools
timeout after three seconds without retry. POST requires JSON and rejects
browser Origin/foreign Host requests. This is a trusted single-user demo,
not an authenticated multi-user IoT security boundary.

Persistent SQLite state/audit: `$HERMES_HOME/demo-home/state.sqlite3`.
Initial ON: living room AC, second bedroom AC, lights, TV.
Initial OFF: main bedroom AC, robot vacuum. Restarts preserve state.

Operator commands (substitute the actual profile path for `$HERMES_HOME`):

```bash
python3 "$HERMES_HOME/plugins/demo-home/simulator.py" status --db "$HERMES_HOME/demo-home/state.sqlite3"
python3 "$HERMES_HOME/plugins/demo-home/simulator.py" reset-demo --db "$HERMES_HOME/demo-home/state.sqlite3"
```

Reset restores demo defaults and retains audit history. It is not an agent tool.
Disable with `systemctl --user disable --now hermes-demo-home.service` and
remove `demo-home` from the enabled plugins; restart Hermes normally.

Fast acceptance: ask "Did I leave any appliances on at home?", then
"Yes, turn them off", then query again. Also test "只打开客厅电视" and
an unrelated question. Status must come from tools; all changes are simulated.
Full acoustic/mixed-topic/model reliability regression remains separate.
