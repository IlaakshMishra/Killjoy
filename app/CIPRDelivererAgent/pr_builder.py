from typing import Callable


def build_pr_body(run_result: dict) -> str:
    status = "✅ passed" if run_result["tests_passed"] else "❌ failed"
    score_pct = round(run_result["score"] * 100, 1)

    lines = [
        "## Killjoy Integration Tests",
        "",
        f"**Sandbox run:** tests {status} against real code.",
        f"**Mutation score:** {score_pct}% ({run_result['killed']} killed / {run_result['killed'] + run_result['survived']} total mutants)",
        "",
    ]

    surviving = run_result.get("surviving_mutants", [])
    if surviving:
        lines.append("### Surviving mutants (not caught by these tests)")
        lines.append("")
        for mutant in surviving:
            lines.append(f"- `{mutant['file']}:{mutant['line']}` — {mutant['description']}")
        lines.append("")
    else:
        lines.append("No mutants survived — every planted mutation was caught.")
        lines.append("")

    lines.append("_This PR was generated automatically by Killjoy and has not been auto-merged. Review before merging._")

    return "\n".join(lines)


def open_pull_request(
    github_post: Callable[[str, dict, dict], dict],
    owner: str,
    repo: str,
    branch: str,
    base: str,
    title: str,
    body: str,
    labels: list[str],
) -> dict:
    if branch == base:
        raise ValueError("refusing to open a PR from a branch onto itself")
    if branch in ("main", "master"):
        raise ValueError("refusing to open a PR from main/master")

    url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    json_body = {"title": title, "head": branch, "base": base, "body": body}

    pr = github_post(url, json_body, headers)

    if labels:
        labels_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr['number']}/labels"
        try:
            github_post(labels_url, {"labels": labels}, headers)
        except Exception as label_exc:
            raise RuntimeError(
                f"PR opened at {pr['html_url']} but applying labels failed: {label_exc}"
            ) from label_exc

    return pr
