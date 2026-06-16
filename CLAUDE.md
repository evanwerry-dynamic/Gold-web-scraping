# Railway log access (Claude Code on the web)

Goal: let Claude query Railway's API directly from a remote session to pull
deployment logs / bot status instead of relying on pasted screenshots.

Status: not yet working as of 2026-06-16. `backboard.railway.app` returns
`403 Host not in allowlist` from the sandbox even after adding it to the
environment's domain allowlist (Settings > Capabilities > Additional allowed
domains). The allowlist only applies to newly-started containers — changing
it mid-session does nothing. To test: start a genuinely new
session/conversation (not just continue an existing one) on this repo, then:

```
curl -s https://backboard.railway.app/graphql/v2 --max-time 5
```

If that returns something other than the allowlist error, connectivity
works. Then a Railway API/project token (Railway dashboard > Project
Settings > Tokens) is needed to actually query the GraphQL API for logs.

Do NOT commit a Railway token to this repo or this file. Pass it as a
session/environment variable instead. A token was pasted directly into a
chat session on 2026-06-16 for testing — it should be treated as
compromised and rotated in Railway.

Separately: the "Railway App" GitHub App installed on this repo (visible
under github.com/settings/installations) is Railway's own auto-deploy
integration (push-to-deploy). It is unrelated to Claude's network access
and already works independently of the above.
