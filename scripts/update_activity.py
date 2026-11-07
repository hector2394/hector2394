import html
import json
import os
import subprocess
from pathlib import Path


ITEM_LIMIT = 4
START = "<!-- activity:start -->"
END = "<!-- activity:end -->"
PULL_REQUEST_QUERY = """
query($query: String!, $after: String) {
  search(query: $query, type: ISSUE, first: 100, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        title url createdAt mergedAt
        repository { nameWithOwner isPrivate }
      }
    }
  }
}
"""
REPOSITORY_QUERY = """
query($owner: String!, $after: String) {
  user(login: $owner) {
    repositories(first: 100, after: $after, privacy: PUBLIC,
                 ownerAffiliations: OWNER, isFork: false) {
      pageInfo { hasNextPage endCursor }
      nodes { nameWithOwner url createdAt pushedAt isPrivate isFork }
    }
  }
}
"""


def fetch_graphql(query: str, variables: dict) -> dict:
    response = subprocess.run(
        ["gh", "api", "graphql", "--input", "-"],
        input=json.dumps({"query": query, "variables": variables}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    payload = json.loads(response.stdout)
    if payload.get("errors"):
        raise RuntimeError(payload["errors"])
    return payload["data"]


def fetch_nodes(query: str, variables: dict, path: tuple[str, ...]) -> list[dict]:
    nodes = []
    after = None
    while True:
        connection = fetch_graphql(query, {**variables, "after": after})
        for key in path:
            connection = connection[key]
        if connection.get("issueCount", 0) > 1000:
            raise RuntimeError("Pull request history exceeds GitHub's search pagination limit")
        nodes.extend(connection["nodes"])
        if not connection["pageInfo"]["hasNextPage"]:
            return nodes
        cursor = connection["pageInfo"]["endCursor"]
        if not cursor or cursor == after:
            raise RuntimeError("GitHub returned an invalid pagination cursor")
        after = cursor


def escape_text(value: str) -> str:
    text = html.escape(" ".join(value.split()), quote=True)
    for character in "\\`*_{}[]()|!#":
        text = text.replace(character, f"\\{character}")
    return text


def render_pull_requests(pulls: list[dict], timestamp: str) -> list[str]:
    public = [pull for pull in pulls if not pull["repository"]["isPrivate"]]
    recent = sorted(public, key=lambda pull: pull[timestamp], reverse=True)[:ITEM_LIMIT]
    return [
        f"- [{escape_text(pull['title'])}]({pull['url']}) "
        f"in {escape_text(pull['repository']['nameWithOwner'])}"
        for pull in recent
    ] or ["No public pull requests yet."]


def render_projects(repositories: list[dict], profile: str) -> list[str]:
    events = []
    for repository in repositories:
        if repository["isPrivate"] or repository["isFork"]:
            continue
        if repository["nameWithOwner"].casefold() == profile.casefold():
            continue
        link = f"[{escape_text(repository['nameWithOwner'])}]({repository['url']})"
        events.append((repository["createdAt"], f"- Created {link}"))
        if repository["pushedAt"] and repository["pushedAt"] > repository["createdAt"]:
            events.append((repository["pushedAt"], f"- Updated {link}"))
    return [line for _, line in sorted(events, reverse=True)[:ITEM_LIMIT]] or [
        "No public projects yet."
    ]


def replace_activity(readme: str, activity: str) -> str:
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise ValueError("README must contain exactly one pair of activity markers")
    before, rest = readme.split(START)
    _, after = rest.split(END)
    return f"{before}{START}\n\n{activity}\n\n{END}{after}"


def main() -> None:
    profile = os.environ["GITHUB_REPOSITORY"]
    owner = profile.split("/")[0]
    opened = fetch_nodes(
        PULL_REQUEST_QUERY,
        {"query": f"author:{owner} is:pr is:public sort:created-desc"},
        ("search",),
    )
    merged = [pull for pull in opened if pull["mergedAt"]]
    repositories = fetch_nodes(REPOSITORY_QUERY, {"owner": owner}, ("user", "repositories"))
    sections = [
        ("Opened pull requests", render_pull_requests(opened, "createdAt")),
        ("Merged pull requests", render_pull_requests(merged, "mergedAt")),
        ("Own projects", render_projects(repositories, profile)),
    ]
    activity = "\n\n".join(f"### {title}\n\n" + "\n".join(lines) for title, lines in sections)
    readme = Path(__file__).resolve().parents[1] / "README.md"
    original = readme.read_text(encoding="utf-8")
    updated = replace_activity(original, activity)
    if updated != original:
        readme.write_text(updated, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
