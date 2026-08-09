import subprocess
from pathlib import Path


def _run_git(args: list[str], cwd: Path) -> None:
    # Always pass an explicit git identity as global options (before the
    # subcommand), not just for `commit`. Docker containers have no
    # resolvable hostname for git's auto-detection to fall back on, so
    # `git commit` fails with "fatal: unable to auto-detect email address"
    # unless user.name/user.email are configured. The -c flag approach works
    # identically on a host with a real ~/.gitconfig and in a bare container,
    # and doesn't require touching the Dockerfile.
    subprocess.run(
        ["git", "-c", "user.name=Killjoy", "-c", "user.email=killjoy@example.com", *args],
        cwd=str(cwd), check=True, capture_output=True, text=True,
    )


def clone_repo(repo_url: str, token: str, dest: Path, ref: str) -> None:
    authenticated_url = repo_url.replace("https://", f"https://x-access-token:{token}@")
    try:
        subprocess.run(["git", "clone", authenticated_url, str(dest)], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        # Sanitize token from error message to prevent leaks in logs
        sanitized_message = str(e).replace(token, "***")
        if e.stderr:
            sanitized_message += f"\nStderr: {e.stderr.replace(token, '***')}"
        raise RuntimeError(sanitized_message) from None
    _run_git(["checkout", ref], dest)


def create_branch_and_commit(repo_path: Path, branch_name: str, files: dict[str, str], commit_message: str) -> None:
    _run_git(["checkout", "-b", branch_name], repo_path)

    for rel_path, content in files.items():
        file_path = repo_path / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        _run_git(["add", rel_path], repo_path)

    _run_git(["commit", "-m", commit_message], repo_path)


def push_branch(repo_path: Path, branch_name: str) -> None:
    _run_git(["push", "origin", branch_name], repo_path)
