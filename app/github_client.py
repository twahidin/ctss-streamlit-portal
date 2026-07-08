"""GitHub commit client. Writes a group's files to the mono-repo as one atomic commit."""

from __future__ import annotations

from github import Github, GithubException
from github.InputGitTreeElement import InputGitTreeElement


class GitHubCommitter:
    """Commits a set of files to one branch as a single tree-based commit.

    Using the lower-level git-data API (blob -> tree -> commit -> ref) means
    the seven-Railway-services setup sees exactly one webhook, not one per file.
    """

    def __init__(self, token: str, repo_name: str, branch: str = "main") -> None:
        self._client = Github(token)
        self._repo = self._client.get_repo(repo_name)
        self._branch = branch

    def commit_group_files(
        self,
        group_id: str,
        files: dict[str, str],
        commit_message: str,
    ) -> str:
        """Write `files` under `group_id/` and return the new commit SHA.

        Files not in `files` are left alone — uploads merge into the folder.
        Path traversal is impossible because filenames are validated upstream
        and `group_id` is sourced from server-side config (not user input).
        """
        ref = self._repo.get_git_ref(f"heads/{self._branch}")
        latest_commit = self._repo.get_git_commit(ref.object.sha)
        base_tree = latest_commit.tree

        tree_elements: list[InputGitTreeElement] = []
        for filename, content in files.items():
            blob = self._repo.create_git_blob(content=content, encoding="utf-8")
            tree_elements.append(
                InputGitTreeElement(
                    path=f"{group_id}/{filename}",
                    mode="100644",
                    type="blob",
                    sha=blob.sha,
                )
            )

        new_tree = self._repo.create_git_tree(tree_elements, base_tree=base_tree)
        new_commit = self._repo.create_git_commit(
            message=commit_message,
            tree=new_tree,
            parents=[latest_commit],
        )
        ref.edit(sha=new_commit.sha)
        return new_commit.sha

    def folder_exists(self, group_id: str) -> bool:
        try:
            self._repo.get_contents(group_id, ref=self._branch)
            return True
        except GithubException:
            return False
