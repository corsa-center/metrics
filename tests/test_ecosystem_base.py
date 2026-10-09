"""Unit tests for RepoTree, the case-insensitive whole-tree path index
shared by collectors (collectors/ecosystem/base.py). URLs and the tree
fetch come from the forge, so these run against GitHubForge with a mocked
client, plus one GitLab URL check."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx

from collectors.ecosystem.base import COLLECTION_GAP, RepoTree
from forge.github import GitHubForge
from forge.gitlab import GitLabForge


def _resp(status_code, json_body=None, headers=None, text=""):
    r = MagicMock(spec=httpx.Response)
    r.status_code = status_code
    r.json.return_value = json_body
    r.headers = httpx.Headers(headers or {})
    r.text = text
    r.aread = AsyncMock()
    return r


class TestRepoTree:
    def _tree(self, paths, truncated=False):
        return RepoTree(GitHubForge(), "o/r", paths, truncated)

    def _fetch(self, tree_response):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, tree_response))
        return asyncio.run(RepoTree.fetch(client, GitHubForge(), "o/r"))

    # -- match(): case-insensitive candidate-list lookup, replacing the old
    # case-sensitive _check_file_exists loop.

    def test_match_is_case_insensitive(self):
        # AMReX-Codes/amrex ships "GOVERNANCE.rst" with different casing than
        # a candidate spelled "governance.rst".
        tree = self._tree(["GOVERNANCE.rst", "README.md"])
        assert tree.match(["governance.rst", "GOVERNANCE.md"]) == "GOVERNANCE.rst"

    def test_match_returns_real_casing_not_the_candidate(self):
        tree = self._tree(["GNUmakefile.in"])
        assert tree.match(["Makefile", "GNUmakefile.in"]) == "GNUmakefile.in"

    def test_match_respects_candidate_order(self):
        tree = self._tree(["GOVERNANCE.md", "docs/governance.md"])
        assert tree.match(["GOVERNANCE.md", "docs/governance.md"]) == "GOVERNANCE.md"

    def test_match_none_when_nothing_present(self):
        tree = self._tree(["README.md"])
        assert tree.match(["GOVERNANCE.md", "docs/GOVERNANCE.md"]) is None

    def test_match_url_renders_a_browsable_link(self):
        tree = self._tree(["Docs/GettingStarted.rst"])
        assert tree.match_url(["docs/GettingStarted.rst"]) == (
            "https://github.com/o/r/blob/HEAD/Docs/GettingStarted.rst"
        )

    def test_match_url_none_when_unmatched(self):
        tree = self._tree(["README.md"])
        assert tree.match_url(["GOVERNANCE.md"]) is None

    def test_match_finds_a_vendored_directory(self):
        # "test/googletest" is a vendored subdirectory, not a file -- the
        # Contents API handled both the same way, so match() has to too.
        tree = self._tree(["test/googletest/googletest/include/gtest/gtest.h"])
        assert tree.match(["test/googletest"]) == "test/googletest"

    def test_match_url_renders_a_tree_link_for_a_directory_hit(self):
        tree = self._tree([".github/ISSUE_TEMPLATE/bug_report.md"])
        assert tree.match_url([".github/ISSUE_TEMPLATE"]) == (
            "https://github.com/o/r/tree/HEAD/.github/ISSUE_TEMPLATE"
        )

    def test_match_prefers_a_file_over_a_directory_of_the_same_name(self):
        # Degenerate case (a repo can't actually have both), but match()
        # should still resolve deterministically rather than pick either.
        tree = self._tree(["docs", "docs/x/y.md"])
        assert tree.match(["docs"]) == "docs"

    # -- has_dir(): case-insensitive directory presence.

    def test_has_dir_case_insensitive(self):
        # superlu ships DOC/, not doc/ or docs/ -- the candidate name "doc"
        # still has to find it.
        tree = self._tree(["DOC/html/index.html", "DOC/CMakeLists.txt"])
        assert tree.has_dir("doc") is True

    def test_has_dir_does_not_match_a_different_name(self):
        tree = self._tree(["DOC/html/index.html"])
        assert tree.has_dir("docs") is False

    def test_has_dir_false_when_absent(self):
        tree = self._tree(["README.md", "src/main.c"])
        assert tree.has_dir("docs") is False

    def test_has_dir_does_not_match_a_file_with_the_same_stem(self):
        # "doc" as a filename (no trailing slash) must not count as a "doc/"
        # directory.
        tree = self._tree(["doc"])
        assert tree.has_dir("doc") is False

    # -- find(): regex search over the whole tree, for "this concept, any
    # spelling" checks a literal list can't express.

    def test_find_matches_anywhere_in_the_tree(self):
        tree = self._tree([
            "Docs/sphinx_documentation/source/GettingStarted.rst",
            "src/main.c",
        ])
        hits = tree.find(r"getting[-_]?started")
        assert hits == ["Docs/sphinx_documentation/source/GettingStarted.rst"]

    def test_find_is_case_insensitive_by_default(self):
        tree = self._tree(["docs/GOVERNANCE.RST"])
        assert tree.find(r"governance\.rst") == ["docs/GOVERNANCE.RST"]

    def test_find_url_first_hit(self):
        tree = self._tree(["a/quickstart.md", "b/quickstart.md"])
        assert tree.find_url(r"quickstart") == "https://github.com/o/r/blob/HEAD/a/quickstart.md"

    def test_find_url_none_when_no_hits(self):
        tree = self._tree(["README.md"])
        assert tree.find_url(r"quickstart") is None

    # -- fetch(): the tree fetch itself.

    def test_fetch_extracts_blob_paths_only(self):
        tree = self._fetch({
            "tree": [
                {"path": "src", "type": "tree"},
                {"path": "src/main.c", "type": "blob"},
                {"path": "README.md", "type": "blob"},
            ],
            "truncated": False,
        })
        assert sorted(tree.paths) == ["README.md", "src/main.c"]
        assert tree.truncated is False

    def test_fetch_carries_truncated_flag(self):
        # llvm/llvm-project is large enough to hit this in practice.
        tree = self._fetch({"tree": [{"path": "a", "type": "blob"}], "truncated": True})
        assert tree.truncated is True

    def test_fetch_non_200_is_a_gap(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(404))
        result = asyncio.run(RepoTree.fetch(client, GitHubForge(), "o/r"))
        assert result is COLLECTION_GAP

    def test_fetch_network_exception_is_a_gap(self):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
        result = asyncio.run(RepoTree.fetch(client, GitHubForge(), "o/r"))
        assert result is COLLECTION_GAP


class TestFindOwned:
    def _tree(self, paths):
        return RepoTree(GitHubForge(), "o/r", paths, truncated=False)

    def test_prefers_the_shallowest_match(self):
        tree = self._tree(["a/b/c/Dockerfile", "docker/Dockerfile"])
        assert tree.find_owned(r"(?:^|/)Dockerfile$") == "docker/Dockerfile"

    def test_skips_vendored_directories(self):
        tree = self._tree(["external/x/Dockerfile", "tpl/y/Dockerfile", "node_modules/z/Dockerfile"])
        assert tree.find_owned(r"(?:^|/)Dockerfile$") is None

    def test_url_for(self):
        assert self._tree([]).url_for("a/b") == "https://github.com/o/r/blob/HEAD/a/b"


class TestRepoTreeOnGitLab:
    def test_urls_use_the_gitlab_host_and_route(self):
        forge = GitLabForge(api_base="https://gitlab.kitware.com/api/v4")
        tree = RepoTree(forge, "paraview/paraview", [".gitlab/issue_templates/Bug.md", "README.md"], False)
        assert tree.url_for("README.md") == "https://gitlab.kitware.com/paraview/paraview/-/blob/HEAD/README.md"
        assert tree.match_url([".gitlab/issue_templates"]) == (
            "https://gitlab.kitware.com/paraview/paraview/-/tree/HEAD/.gitlab/issue_templates")


class TestRepoTreeCache:
    """About ten collectors ask for the same package's tree; on GitLab that
    is up to hundreds of paged requests, so it is fetched once per package."""

    def _counting_forge(self, result=None):
        from tests.fakes import FakeForge
        forge = FakeForge(files={"README.md": "x"})
        calls = []
        original = forge.repo_tree

        async def repo_tree(client, ref):
            calls.append(ref)
            return result if result is not None else await original(client, ref)
        forge.repo_tree = repo_tree
        return forge, calls

    def test_second_fetch_is_served_from_cache(self):
        forge, calls = self._counting_forge()

        async def go():
            a = await RepoTree.fetch(None, forge, "o/r")
            b = await RepoTree.fetch(None, forge, "o/r")
            return a, b
        a, b = asyncio.run(go())
        assert a is b and calls == ["o/r"]

    def test_concurrent_fetches_share_one_request(self):
        forge, calls = self._counting_forge()

        async def go():
            return await asyncio.gather(*[RepoTree.fetch(None, forge, "o/r") for _ in range(5)])
        trees = asyncio.run(go())
        assert len(calls) == 1 and all(t is trees[0] for t in trees)

    def test_gaps_are_not_cached(self):
        forge, calls = self._counting_forge(result=COLLECTION_GAP)

        async def go():
            return [await RepoTree.fetch(None, forge, "o/r") for _ in range(2)]
        assert asyncio.run(go()) == [COLLECTION_GAP, COLLECTION_GAP]
        assert len(calls) == 2

    def test_different_repositories_are_cached_separately(self):
        forge, calls = self._counting_forge()

        async def go():
            await RepoTree.fetch(None, forge, "o/r")
            await RepoTree.fetch(None, forge, "o/other")
        asyncio.run(go())
        assert calls == ["o/r", "o/other"]
