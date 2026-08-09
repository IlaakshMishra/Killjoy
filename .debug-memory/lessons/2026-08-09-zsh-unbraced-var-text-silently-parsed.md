---
id: "2026-08-09-zsh-unbraced-var-text-silently-parsed"
title: "zsh unbraced $var:text silently parsed as history/parameter modifier, mangling docker tags"
domain:
  - "shell"
  - "zsh"
  - "docker"
  - "ci-cd"
error_signature: "docker push fails with \"unknown: The repository with name '&lt;name&gt;atest' does not exist in the registry\" after a zsh for-loop builds a tag like \"$var:latest\""
created_at: "2026-08-09T13:45:15Z"
confidence: confirmed
---

## Symptom

A zsh for-loop built and tagged docker images with `-t "registry/repo-$agent:latest"`. The build/push commands ran with no shell error, but the resulting tag was silently mangled, e.g. "killjoy-environment-mapper:latest" became "killjoy-environment-mapperatest" — the colon and the letter "l" both vanished. ECR push then failed with a 404-style "repository does not exist" error referencing the mangled name.

## Approaches that FAILED (do not repeat)

- Using bash associative-array key iteration `${!AGENT_DIRS[@]}` — in an interactive zsh terminal this broke first for a different reason: zsh's interactive history expansion treats leading `!` specially even inside double quotes, mangling the string into `"${"` and leaving the shell stuck at a `dquote braceparam dquote>` continuation prompt before the loop even ran.
- Running `set +H` to disable history expansion fixed the interactive-terminal case, but that shell-option state does not persist across separate tool-call invocations (each is a fresh non-interactive shell), so the fix silently stopped applying in the next command.
- Switching to a portable 'key:value' string-pair loop (avoiding bash associative-array syntax entirely) fixed the history-expansion problem but exposed a second, unrelated zsh bug: unbraced `"$agent:latest"` was parsed as zsh parameter-expansion modifier syntax.

## Root cause

zsh applies history/parameter modifier syntax (`:l` lowercase, `:u` uppercase, `:h`, `:t`, `:r`, `:e`, etc.) to ANY unbraced `$var:text` construct where `text` begins with a recognized modifier letter — not only to history event references like `!$` or `!!`. Since "latest" starts with "l", zsh consumed `:l` as the lowercase modifier and left the literal remainder "atest" appended directly to the variable's value, with no parse error. Bash has no equivalent behavior, so this is a zsh-specific footgun that produces a silently wrong string rather than a loud failure.

## Fix

Always brace-delimit a shell variable that is immediately followed by a literal colon in zsh: use `"${agent}:latest"` instead of `"$agent:latest"`. Braces terminate the variable name before the colon, so zsh treats the colon and everything after it as literal text rather than a modifier. Verified: after switching all loop variables to braced form (`${agent}`, `${dir}`), all 5 `docker buildx build --push` commands produced correctly named tags, confirmed via `aws ecr describe-images --repository-name <repo> --query 'imageDetails[].imageTags'` returning `latest` for every repository.

## Tags for retrieval

- shell
- zsh
- docker
- ci
- cd
- push
- fails
- unknown
- repository
- name
- lt
- gt
- atest
- does
- not
- exist
- registry
- after
- loop
- builds
- tag
- like
- var
- latest
- unbraced
- text
- silently
- parsed
- history
- parameter
- modifier
- mangling
- tags
