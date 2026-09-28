"""Tests for what a native tool leaves behind, and for the tasks that invoke one.

Every case here is something a native tool wrote that a layer owns, or a script a
layer shipped without wiring. Both are junk in a fresh repository; one was data loss.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "templates"
NATIVE_INIT = REPO / "tools/tasks/native_init.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def native_init():
    return load_module(NATIVE_INIT, "native_init_under_test")


def fake_tool(native_init, monkeypatch, writes: dict[str, str]) -> list[list[str]]:
    """Stand in for the native tool: record the command, write these files."""
    calls: list[list[str]] = []

    def run(cmd, owns=""):
        calls.append(cmd)
        for name, text in writes.items():
            if not Path(name).exists():
                Path(name).parent.mkdir(parents=True, exist_ok=True)
                Path(name).write_text(text)
        return 0

    monkeypatch.setattr(native_init, "run", run)
    return calls


def test_the_crate_is_named_after_the_project_not_the_directory(native_init, tmp_path, monkeypatch):
    """`cargo init` names the package after its directory: a scaffold into `ref/` failed
    the whole apply on a Rust keyword, and any other name disagreed with PROJECT_NAME."""
    monkeypatch.chdir(tmp_path)
    calls = fake_tool(native_init, monkeypatch, {"Cargo.toml": '[package]\nname = "my-app"\n'})
    monkeypatch.setattr("sys.argv", ["native_init.py", "rust", "my-app:bin", "MIT"])

    assert native_init.main() == 0
    assert calls == [["cargo", "init", "--name", "my-app", "--bin", "--quiet"]]


def test_an_interrupted_init_is_finished_by_the_next_run(native_init, tmp_path, monkeypatch):
    """An apply killed between `bun init` and the reconciliation left a package.json
    with no dev tools and bun's CLAUDE.md in place. Every later apply skipped the
    manifest as present, folded bun's CLAUDE.md into AGENTS.md, and reported clean."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / native_init.PENDING.format(kind="ts")).write_text("[]\n")
    (tmp_path / "package.json").write_text('{"name": "tmp"')  # cut off mid-write
    (tmp_path / "CLAUDE.md").write_text("bun's generic agent file\n")
    fake_tool(native_init, monkeypatch, {"package.json": '{"name": "tmp"}\n'})
    monkeypatch.setattr("sys.argv", ["native_init.py", "ts", "my-app", "oxlint=1.0.0"])

    assert native_init.main() == 0

    assert not (tmp_path / "CLAUDE.md").exists(), "bun's file survived the recovery"
    manifest = json.loads((tmp_path / "package.json").read_text())
    assert manifest["name"] == "my-app"
    assert "oxlint" in manifest["devDependencies"]
    assert not (tmp_path / native_init.PENDING.format(kind="ts")).exists()


def test_a_file_that_existed_before_an_interrupted_init_is_still_kept(
    native_init, tmp_path, monkeypatch
):
    """By the second run bun's own leftovers look pre-existing too; the record of what
    was there first is what keeps a repository's CLAUDE.md."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / native_init.PENDING.format(kind="ts")).write_text('["CLAUDE.md"]\n')
    (tmp_path / "CLAUDE.md").write_text("the repository's own\n")
    fake_tool(native_init, monkeypatch, {"package.json": '{"name": "tmp"}\n'})
    monkeypatch.setattr("sys.argv", ["native_init.py", "ts", "my-app", ""])

    assert native_init.main() == 0
    assert (tmp_path / "CLAUDE.md").read_text() == "the repository's own\n"


def test_a_manifest_with_no_pending_marker_is_still_left_alone(native_init, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "go.mod").write_text("module mine\n")
    calls = fake_tool(native_init, monkeypatch, {})
    monkeypatch.setattr("sys.argv", ["native_init.py", "go", "my-app"])

    assert native_init.main() == 0
    assert calls == []
    assert (tmp_path / "go.mod").read_text() == "module mine\n"


def test_go_mod_says_the_pinned_version_and_ignores_node_modules(
    native_init, tmp_path, monkeypatch
):
    """`go mod init` wrote the Go that ran it (1.27.1 against a 1.26 pin), and `./...`
    walked into a CDK app's node_modules, whose `%name%.template.go` files do not parse:
    golangci-lint and `go test` both failed the scaffold's own `just check`."""
    monkeypatch.chdir(tmp_path)
    fake_tool(native_init, monkeypatch, {"go.mod": "module my-app\n\ngo 1.27.1\n"})
    monkeypatch.setattr("sys.argv", ["native_init.py", "go", "my-app", "1.26"])

    assert native_init.main() == 0

    lines = (tmp_path / "go.mod").read_text().splitlines()
    assert "go 1.26" in lines and "go 1.27.1" not in lines
    assert "ignore node_modules" in lines


def test_the_go_format_recipe_stays_out_of_node_modules(tmp_path):
    """`gofmt -w .` rewrote files inside node_modules and failed on the ones that do
    not parse. Runs the recipe's own command line against such a tree."""
    import shutil
    import subprocess

    if shutil.which("gofmt") is None:
        pytest.skip("gofmt is not installed")
    recipe = (TEMPLATES / "lang-go/.just.d/go.just.jinja").read_text().split("go-fmt:\n", 1)[1]
    command = recipe.splitlines()[0].strip().replace("-w", "-l")
    (tmp_path / "main.go").write_text("package main\nfunc main(){}\n")
    broken = tmp_path / "infra/node_modules/aws-cdk/%name%.template.go"
    broken.parent.mkdir(parents=True)
    broken.write_text("package %name%\n")

    done = subprocess.run(command, shell=True, cwd=tmp_path, capture_output=True, text=True)

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "./main.go"


def test_bun_leftovers_are_removed_when_bun_created_them(native_init, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "package.json").write_text('{"name": "wrong-name"}\n')
    (tmp_path / "CLAUDE.md").write_text("bun's generic agent file\n")
    (tmp_path / ".gitignore").write_text("node_modules\n")
    (tmp_path / "bun.lock").write_text("{}\n")

    native_init._tidy_after_bun("my-app", pre_existing=set())

    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / ".gitignore").exists()
    assert not (tmp_path / "bun.lock").exists()
    assert '"name": "my-app"' in (tmp_path / "package.json").read_text()


def test_a_file_the_repository_already_had_is_never_deleted(native_init, tmp_path, monkeypatch):
    """Deleting by filename took a brownfield repo's own CLAUDE.md with it.

    `bun init` only runs when there is no package.json, and a repository can have
    hand-written agent instructions and no package.json at the same time.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "package.json").write_text('{"name": "my-app"}\n')
    (tmp_path / "CLAUDE.md").write_text("MY instructions, hand written\n")
    (tmp_path / ".gitignore").write_text("/my-own-ignores\n")

    native_init._tidy_after_bun("my-app", pre_existing={"CLAUDE.md", ".gitignore"})

    assert (tmp_path / "CLAUDE.md").read_text() == "MY instructions, hand written\n"
    assert (tmp_path / ".gitignore").read_text() == "/my-own-ignores\n"


def test_a_steering_symlink_is_left_alone(native_init, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "package.json").write_text('{"name": "my-app"}\n')
    (tmp_path / "AGENTS.md").write_text("the real one\n")
    (tmp_path / "CLAUDE.md").symlink_to("AGENTS.md")

    native_init._tidy_after_bun("my-app", pre_existing=set())

    assert (tmp_path / "CLAUDE.md").is_symlink()


# ------------------------------------------------------------------- python wiring


def test_uv_leaves_no_second_interpreter_pin(native_init, tmp_path, monkeypatch):
    """`.python-version` and .mise/conf.d/python.toml would drift apart."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".python-version").write_text("3.12\n")

    native_init._tidy_after_uv(set())

    assert not (tmp_path / ".python-version").exists()


def test_an_interpreter_pin_the_repository_had_is_kept(native_init, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".python-version").write_text("3.11\n")

    native_init._tidy_after_uv({".python-version"})

    assert (tmp_path / ".python-version").read_text() == "3.11\n"


def test_uvs_placeholder_function_is_emptied(native_init, tmp_path, monkeypatch):
    """ruff rejects logic in `__init__`, so uv's own `hello()` failed `just check`."""
    monkeypatch.chdir(tmp_path)
    package = tmp_path / "src/my_app"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('def hello() -> str:\n    return "Hello from my-app!"\n')

    native_init._strip_uv_placeholder()

    assert (package / "__init__.py").read_text() == '"""my_app."""\n'


def test_a_real_init_module_is_left_alone(native_init, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    package = tmp_path / "src/my_app"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('from .core import run\n\n__all__ = ["run"]\n')

    native_init._strip_uv_placeholder()

    assert "from .core import run" in (package / "__init__.py").read_text()


def test_the_tools_the_recipes_call_are_declared(native_init, tmp_path, monkeypatch):
    """`uv init` writes no dependency group, so `uv run ty` had nothing to run."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "my-app"\n')

    native_init._declare_dev_tools()

    manifest = (tmp_path / "pyproject.toml").read_text()
    assert "[dependency-groups]" in manifest
    for tool in ("ruff", "ty", "pytest", "deptry", "nox"):
        assert f'"{tool}",' in manifest


def test_an_existing_dependency_group_is_not_duplicated(native_init, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = '[project]\nname = "x"\n\n[dependency-groups]\ndev = ["pytest"]\n'
    (tmp_path / "pyproject.toml").write_text(before)

    native_init._declare_dev_tools()

    assert (tmp_path / "pyproject.toml").read_text() == before


def test_a_brownfield_pyproject_still_gets_the_dev_tools(native_init, tmp_path, monkeypatch):
    """`main()` used to skip the whole py branch when pyproject.toml pre-existed,
    including the dev-tool declaration -- so `uv run ruff` in a brownfield repository
    had nothing to run, silently, with no warning naming the gap."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "brownfield"\n')
    monkeypatch.setattr("sys.argv", ["native_init.py", "py", "brownfield:src:3.12"])

    def fail_if_called(cmd, owns=""):
        raise AssertionError(f"uv should not run against an existing manifest: {cmd}")

    monkeypatch.setattr(native_init, "run", fail_if_called)

    assert native_init.main() == 0

    manifest = (tmp_path / "pyproject.toml").read_text()
    assert "[dependency-groups]" in manifest
    assert 'name = "brownfield"' in manifest  # the repository's own name is untouched


def test_an_empty_test_run_is_not_left_to_fail_the_gate(native_init, tmp_path, monkeypatch):
    """pytest exits 5 with nothing collected, and the layer's pytest.ini makes the
    missing-testpaths warning an error, so a fresh scaffold was red either way."""
    monkeypatch.chdir(tmp_path)
    package = tmp_path / "src/my_app"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('"""my_app."""\n')

    native_init._seed_python_test()

    assert "import my_app" in (tmp_path / "tests/test_smoke.py").read_text()


def test_a_project_with_tests_gets_no_seeded_one(native_init, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    package = tmp_path / "src/my_app"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_real.py").write_text("def test_x(): pass\n")

    native_init._seed_python_test()

    assert not (tmp_path / "tests/test_smoke.py").exists()


# ----------------------------------------------------------------------- go wiring


def test_go_gets_a_package_because_its_own_tool_writes_none(native_init, tmp_path, monkeypatch):
    """`go mod init` writes no source: `go test ./...` exited 1 and golangci-lint 5."""
    monkeypatch.chdir(tmp_path)

    native_init._seed_go_package("my-app")

    assert 'fmt.Println("my-app")' in (tmp_path / "main.go").read_text()


def test_a_repository_that_already_has_go_sources_is_left_alone(native_init, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cmd").mkdir()
    (tmp_path / "cmd/serve.go").write_text("package main\n")

    native_init._seed_go_package("my-app")

    assert not (tmp_path / "main.go").exists()


# ----------------------------------------------------------------------- ts wiring


def test_the_ts_tools_are_pinned_into_the_manifest(native_init, tmp_path, monkeypatch):
    """Without them `bunx biome` fell through to PATH, and oxlint refused to start
    because `typeAware: true` needs oxlint-tsgolint."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "package.json").write_text('{"name": "my-app"}\n')

    native_init._declare_ts_dev_tools("@biomejs/biome=2.4.1,oxlint-tsgolint=7.0.2002")

    dev = json.loads((tmp_path / "package.json").read_text())["devDependencies"]
    assert dev == {"@biomejs/biome": "2.4.1", "oxlint-tsgolint": "7.0.2002"}


def test_a_version_the_project_already_chose_is_not_overwritten(native_init, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "package.json").write_text('{"devDependencies": {"oxlint": "1.0.0"}}\n')

    native_init._declare_ts_dev_tools("oxlint=1.85.0")

    dev = json.loads((tmp_path / "package.json").read_text())["devDependencies"]
    assert dev == {"oxlint": "1.0.0"}


def test_a_brownfield_package_json_still_gets_the_dev_tools(native_init, tmp_path, monkeypatch):
    """`main()` used to skip the whole ts branch when package.json pre-existed,
    including the dev-tool declaration -- so `bunx biome` in a brownfield repository
    fell through to PATH or a mise shim, unversioned, with no warning naming the gap."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "brownfield-widget", "scripts": {"test": "vitest run"}}) + "\n"
    )
    monkeypatch.setattr("sys.argv", ["native_init.py", "ts", "brownfield-widget", "oxlint=1.85.0"])

    def fail_if_called(cmd, owns=""):
        raise AssertionError(f"bun should not run against an existing manifest: {cmd}")

    monkeypatch.setattr(native_init, "run", fail_if_called)

    assert native_init.main() == 0

    data = json.loads((tmp_path / "package.json").read_text())
    assert data["devDependencies"] == {"oxlint": "1.85.0"}
    assert data["scripts"] == {"test": "vitest run"}  # the repository's own script survives


def test_the_entry_point_bun_names_keeps_existing(native_init, tmp_path, monkeypatch):
    """package.json names index.ts as the module. Deleting it broke tsc, bun test
    and knip at once, so the contents are replaced instead."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "index.ts").write_text('console.log("Hello via Bun!");\n')

    native_init._seed_ts_entry_point()

    assert "Hello via Bun" not in (tmp_path / "index.ts").read_text()
    assert "export function greet" in (tmp_path / "index.ts").read_text()
    assert 'from "./index"' in (tmp_path / "index.test.ts").read_text()


def test_a_written_entry_point_is_never_replaced(native_init, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "index.ts").write_text("export const handler = 1;\n")

    native_init._seed_ts_entry_point()

    assert (tmp_path / "index.ts").read_text() == "export const handler = 1;\n"
    assert not (tmp_path / "index.test.ts").exists()


def test_the_lockfile_bun_wrote_before_the_edits_is_dropped(native_init):
    """`bun install --frozen-lockfile` failed on a scaffold nobody had touched."""
    assert "bun.lock" in dict(native_init.BUN_LEFTOVERS)
    assert "index.ts" not in dict(native_init.BUN_LEFTOVERS)


# ------------------------------------------------------------------- layer wiring


@pytest.mark.parametrize(
    ("layer", "manifest"),
    [
        ("lang-python", "pyproject.toml"),
        ("lang-go", "go.mod"),
        ("lang-rust", "Cargo.toml"),
        ("lang-ts", "package.json"),
    ],
)
def test_every_language_layer_initialises_its_manifest(layer, manifest):
    """python and go shipped no manifest and wired no task, so `just setup` failed:
    "No `pyproject.toml` found" and "go: no modules specified"."""
    import yaml

    cfg = yaml.safe_load((TEMPLATES / layer / "copier.yml").read_text())
    commands = [" ".join(t["command"]) for t in cfg.get("_tasks", [])]
    assert any("native_init.py" in c for c in commands), f"{layer} runs no native init"
    assert (TEMPLATES / layer / "tasks/native_init.py").is_file()


def test_a_task_naming_an_uninstalled_script_fails_the_port():
    """The command table and the install table are declared apart. Wiring one without
    the other placed every file and then died with "can't open file"."""
    port = load_module(REPO / "tools/port_assets.py", "port_task_check")
    port.TASKS["probe-layer"] = [
        {"command": ["@@ _copier_python @@", "@@ _copier_conf.src_path @@/tasks/nope.py"]}
    ]
    try:
        with pytest.raises(SystemExit) as raised:
            port.check_task_scripts_installed()
        assert "tasks/nope.py" in str(raised.value)
        assert "TASK_SCRIPTS" in str(raised.value)
    finally:
        del port.TASKS["probe-layer"]


def test_the_shipped_biome_config_is_one_biome_accepts():
    """`"preset": "none"` is not a biome key. It made biome refuse to start, which
    nothing noticed because `bunx biome` could not resolve biome either."""
    config = json.loads((TEMPLATES / "lang-ts/biome.json.jinja").read_text())
    rules = config["linter"]["rules"]
    assert "preset" not in rules
    assert rules["recommended"] is False


def test_the_type_checker_skips_the_plumbing_it_did_not_write():
    """ty reported five errors in scripts/gen_caller.py in a fresh scaffold. ruff.toml
    already exempts scripts/ for the same reason."""
    config = (TEMPLATES / "lang-python/ty.toml.jinja").read_text()
    assert 'exclude = ["scripts"]' in config


def test_an_empty_nextest_run_is_not_a_failure():
    """nextest exits 4 on an empty run and `cargo init --bin` writes no test."""
    fragment = (TEMPLATES / "lang-rust/.just.d/rust.just").read_text()
    invocations = [line for line in fragment.splitlines() if line.startswith("    cargo ")]
    assert [
        line for line in invocations if "nextest" in line and "--no-tests=pass" not in line
    ] == []
    assert len([line for line in invocations if "nextest" in line]) == 2


# ----------------------------------------------------------------- aws cdk wiring


def test_the_cdk_layer_wires_the_generator_it_ships():
    """The script was shipped, documented nowhere, and referenced by no task.

    `just aws-cdk-synth` then failed on a directory nothing had created, and
    `just check` failed with it, in every fresh web-app scaffold.
    """
    fragment = (TEMPLATES / "infra-aws-cdk/.just.d/aws-cdk.just.jinja").read_text()
    assert "aws-cdk-init:" in fragment
    assert "scripts/init_aws_cdk.py" in fragment
    assert "--cdk-version @@AWS_CDK_VERSION@@" in fragment


def test_the_cdk_aggregate_degrades_before_the_app_is_generated():
    fragment = (TEMPLATES / "infra-aws-cdk/.just.d/aws-cdk.just.jinja").read_text()
    aggregate = fragment.split("aws-cdk:")[-1]
    assert "aws-cdk-init" in aggregate
    assert "exit 0" in aggregate


def test_cdk_init_names_the_tool_that_is_missing(tmp_path, monkeypatch):
    """A missing binary used to surface as a CalledProcessError traceback."""
    script = load_module(
        TEMPLATES / "infra-aws-cdk/scripts/init_aws_cdk.py", "init_aws_cdk_under_test"
    )
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(SystemExit) as raised:
        script.run(["definitely-not-a-real-binary"], cwd=tmp_path, what="the cdk generator")
    assert "definitely-not-a-real-binary" in str(raised.value)
    assert "the cdk generator" in str(raised.value)


def test_cdk_init_removes_the_npmignore_it_does_not_need():
    """`cdk init` writes one because its template is an npm package. This is not one."""
    script = load_module(
        TEMPLATES / "infra-aws-cdk/scripts/init_aws_cdk.py", "init_aws_cdk_leftovers"
    )
    assert ".npmignore" in script.LEFTOVERS


def test_cdk_destination_stays_inside_the_repository(tmp_path):
    script = load_module(TEMPLATES / "infra-aws-cdk/scripts/init_aws_cdk.py", "init_aws_cdk_dest")
    root = tmp_path.resolve()
    # The message is asserted, not just the exception: a bare `raises(SystemExit)` is
    # satisfied by any exit, including one from an unrelated fault in destination(),
    # and this is the guard that keeps a generated app inside the repository.
    for escape in ("../escape", os.sep + "absolute", "a/../../b", ""):
        with pytest.raises(SystemExit, match="repository-relative path without dot segments"):
            script.destination(root, escape)
    assert script.destination(root, "infrastructure") == root / "infrastructure"
    assert script.destination(root, "deploy/cdk") == root / "deploy/cdk"


# --------------------------------------------------- the agents index generator
#
# Every case below is a brownfield repository. `run_generators` calls this script
# with the destination and nothing else, so a refusal that names `--claude` names a
# recovery no `project-setup apply` user can reach: the apply exits non-zero with a
# scaffolded repository behind it and its placeholder report suppressed.

AGENTS_INDEX = TEMPLATES / "steering/scripts/install_agents_index.py"


@pytest.fixture
def agents_index():
    return load_module(AGENTS_INDEX, "install_agents_index_under_test")


def scaffolded(tmp_path: Path) -> Path:
    body = tmp_path / "docs/agents/AGENTS.body.md"
    body.parent.mkdir(parents=True)
    body.write_text("# my-app\n\n## Read for\n\nthe generated body\n")
    return body


def test_a_hand_written_claude_md_is_merged_not_refused(agents_index, tmp_path):
    """The whole apply used to fail here, and the fix it named was unreachable.

    AGENTS.md already keeps hand-written text below the body, so refusing for
    CLAUDE.md made the two destinations disagree for no reason a user could act on.
    """
    body = scaffolded(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# CLAUDE.md\nHand written, load bearing.\n")

    agents_index.install_index(tmp_path / "AGENTS.md", body, None)
    agents_index.install_link(tmp_path / "AGENTS.md", tmp_path / "CLAUDE.md", None)

    index = (tmp_path / "AGENTS.md").read_text()
    assert "Hand written, load bearing." in index
    assert "the generated body" in index
    assert (tmp_path / "CLAUDE.md").readlink() == Path("AGENTS.md")


def test_a_claude_md_symlink_elsewhere_still_refuses_but_names_a_runnable_command(
    agents_index, tmp_path
):
    """A symlink is another tool's wiring, not content, so there is no safe default.

    The refusal has to name something the user can actually run, and the script is
    installed in the scaffolded repository precisely so that it can.
    """
    body = scaffolded(tmp_path)
    (tmp_path / "other.md").write_text("another tool's file\n")
    (tmp_path / "CLAUDE.md").symlink_to("other.md")
    agents_index.install_index(tmp_path / "AGENTS.md", body, None)

    with pytest.raises(SystemExit) as raised:
        agents_index.install_link(tmp_path / "AGENTS.md", tmp_path / "CLAUDE.md", None)

    message = str(raised.value)
    assert "scripts/install_agents_index.py" in message
    assert "--claude SKIP" in message
    assert (tmp_path / "CLAUDE.md").readlink() == Path("other.md")


def test_an_agents_md_symlink_refusal_names_a_runnable_command(agents_index, tmp_path):
    body = scaffolded(tmp_path)
    (tmp_path / "shared.md").write_text("shared instructions\n")
    (tmp_path / "AGENTS.md").symlink_to("shared.md")

    with pytest.raises(SystemExit) as raised:
        agents_index.install_index(tmp_path / "AGENTS.md", body, None)

    message = str(raised.value)
    assert "scripts/install_agents_index.py" in message
    assert "--agents SKIP" in message


def test_bds_own_copy_of_agents_md_needs_no_merge(agents_index, tmp_path):
    """Content AGENTS.md already carries is not a second authority."""
    body = scaffolded(tmp_path)
    agents_index.install_index(tmp_path / "AGENTS.md", body, None)
    index_text = (tmp_path / "AGENTS.md").read_text()
    (tmp_path / "CLAUDE.md").write_text(index_text)

    agents_index.install_link(tmp_path / "AGENTS.md", tmp_path / "CLAUDE.md", None)

    assert (tmp_path / "AGENTS.md").read_text() == index_text
    assert (tmp_path / "CLAUDE.md").readlink() == Path("AGENTS.md")


def test_skip_still_leaves_a_hand_written_claude_md_alone(agents_index, tmp_path):
    body = scaffolded(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("mine\n")
    agents_index.install_index(tmp_path / "AGENTS.md", body, None)

    agents_index.install_link(tmp_path / "AGENTS.md", tmp_path / "CLAUDE.md", "SKIP")

    assert (tmp_path / "CLAUDE.md").read_text() == "mine\n"
    assert "mine" not in (tmp_path / "AGENTS.md").read_text()


# ------------------------------------------------- a pinned scaffold pins everything


def test_the_bun_types_version_is_pinned_not_latest(tmp_path: Path):
    """`bun init` writes `"@types/bun": "latest"`, which was the one floating version in
    a scaffold whose every other dependency is exact. bun.lock pins it once setup has
    run, but the manifest still said latest, so a fresh resolve elsewhere could take a
    different version of the types that define the runtime.
    """
    native = load_module(NATIVE_INIT, "native_init_bun_types")
    manifest = tmp_path / "package.json"
    manifest.write_text(json.dumps({"devDependencies": {"@types/bun": "latest"}}) + "\n")

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        native._declare_ts_dev_tools("@types/bun=1.4.2")
    finally:
        os.chdir(cwd)

    assert json.loads(manifest.read_text())["devDependencies"]["@types/bun"] == "1.4.2"


def test_a_deliberate_range_is_not_overwritten(tmp_path: Path):
    """Only `latest` exactly is replaced. A brownfield repository's `^2.0.0` is a choice
    somebody made, and this task is not the place to overrule it."""
    native = load_module(NATIVE_INIT, "native_init_range")
    manifest = tmp_path / "package.json"
    manifest.write_text(json.dumps({"devDependencies": {"@types/bun": "^2.0.0"}}) + "\n")

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        native._declare_ts_dev_tools("@types/bun=1.4.2")
    finally:
        os.chdir(cwd)

    assert json.loads(manifest.read_text())["devDependencies"]["@types/bun"] == "^2.0.0"


UV_FRESH = (
    "[project]\n"
    'name = "mine"\n'
    'version = "0.1.0"\n'
    'description = "Add your description here"\n'
    'readme = "README.md"\n'
    'requires-python = ">=3.12"\n'
)


def test_the_answered_description_and_license_reach_the_manifest(
    native_init, tmp_path, monkeypatch
):
    """`uv init` owns pyproject.toml and writes neither.

    Found by scaffolding this repository with its own tool. uv writes its own
    placeholder description and no license field at all, so a py scaffold shipped a
    wheel stating neither the DESCRIPTION the interview hard-requires nor the license
    the governance layer had just written to disk as LICENSE.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(UV_FRESH)

    native_init._declare_package_metadata("Apache-2.0", "What this project is")

    parsed = tomllib.loads((tmp_path / "pyproject.toml").read_text())
    assert parsed["project"]["description"] == "What this project is"
    assert parsed["project"]["license"] == "Apache-2.0"


def test_a_description_with_toml_metacharacters_survives_the_round_trip(
    native_init, tmp_path, monkeypatch
):
    """The description is free text pasted into a TOML basic string.

    A colon because it travels packed behind the SPDX id, and a quote and a backslash
    because both terminate or escape a TOML string if written literally.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(UV_FRESH)
    hostile = 'A probe: with a "quote", a backslash \\ and trailing text'

    native_init._declare_package_metadata("MIT", hostile)

    parsed = tomllib.loads((tmp_path / "pyproject.toml").read_text())
    assert parsed["project"]["description"] == hostile


def test_the_license_line_is_valid_toml_not_an_escaped_replacement(
    native_init, tmp_path, monkeypatch
):
    """The first version of this used re.sub and wrote `license = \\"MIT\\"`.

    re.sub reinterprets backslashes in its replacement string, so the manifest came out
    with literal backslashes around the value: not valid TOML, and every consumer of the
    scaffold would have failed to parse it. tomllib is the assertion because eyeballing
    the line is exactly what missed it.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(UV_FRESH)

    native_init._declare_package_metadata("MIT", "x")

    body = (tmp_path / "pyproject.toml").read_text()
    assert 'license = "MIT"' in body
    assert "\\" not in body.split("license =")[1].split("\n")[0]
    tomllib.loads(body)  # raises TOMLDecodeError if the escaping regressed


def test_no_license_field_is_written_when_the_project_states_none(
    native_init, tmp_path, monkeypatch
):
    """SPDX_ID=NONE writes no LICENSE file, so claiming one in metadata would lie."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(UV_FRESH)

    native_init._declare_package_metadata(native_init.NO_LICENSE, "still described")

    parsed = tomllib.loads((tmp_path / "pyproject.toml").read_text())
    assert "license" not in parsed["project"]
    assert parsed["project"]["description"] == "still described"


def test_a_brownfield_description_is_kept_and_only_the_license_is_added(
    native_init, tmp_path, monkeypatch, capsys
):
    """A description somebody wrote is theirs; the edit is conditional on uv's placeholder.

    The report is asserted too, because the first version derived it from "was a value
    supplied" and announced setting a description it had correctly left alone. Saying
    what did not happen is the same defect class as a silent skip.
    """
    monkeypatch.chdir(tmp_path)
    mine = UV_FRESH.replace("Add your description here", "The description I wrote")
    (tmp_path / "pyproject.toml").write_text(mine)

    native_init._declare_package_metadata("Apache-2.0", "what the tool was given")

    parsed = tomllib.loads((tmp_path / "pyproject.toml").read_text())
    assert parsed["project"]["description"] == "The description I wrote"
    assert parsed["project"]["license"] == "Apache-2.0"
    said = capsys.readouterr().out
    assert "license = Apache-2.0" in said
    assert "description" not in said, f"reported setting what it kept: {said!r}"


def test_a_license_the_manifest_already_declares_is_not_duplicated(
    native_init, tmp_path, monkeypatch
):
    """`license-files` is a different key, so the guard matches the assignment."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        UV_FRESH.replace('readme = "README.md"\n', 'license = "MIT"\nlicense-files = ["LICENSE"]\n')
    )

    native_init._declare_package_metadata("Apache-2.0", "x")

    body = (tmp_path / "pyproject.toml").read_text()
    assert body.count("license =") == 1, "a second license assignment was added"
    assert tomllib.loads(body)["project"]["license"] == "MIT"


def test_the_python_task_is_given_the_license_and_the_description():
    """A helper nothing calls with the right arguments is a helper that does nothing.

    The two tests above exercise the function directly, which passes whether or not the
    generated copier.yml actually hands it SPDX_ID and DESCRIPTION.
    """
    command = (TEMPLATES / "lang-python/copier.yml").read_text()
    assert "@@ SPDX_ID @@:@@ DESCRIPTION @@" in command, (
        "the py task no longer receives the license and description"
    )


BUN_FRESH = '{\n  "name": "mine",\n  "module": "index.ts",\n  "private": true\n}\n'
CARGO_FRESH = '[package]\nname = "mine"\nversion = "0.1.0"\nedition = "2024"\n\n[dependencies]\n'


def test_the_npm_manifest_states_the_description_and_license(native_init, tmp_path, monkeypatch):
    """`bun init -y` writes neither, so a packed tarball claimed nothing.

    Same gap the py branch had: the governance layer writes LICENSE from SPDX_ID and
    the manifest stayed silent about it.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "package.json").write_text(BUN_FRESH)

    native_init._declare_npm_metadata("MIT", "what this package is")

    data = json.loads((tmp_path / "package.json").read_text())
    assert data["description"] == "what this package is"
    assert data["license"] == "MIT"
    assert data["name"] == "mine", "the manifest's own fields were not preserved"


def test_the_cargo_manifest_states_what_publish_requires(native_init, tmp_path, monkeypatch):
    """`cargo publish` refuses a crate declaring no description and no license.

    So every scaffolded crate was unpublishable, which is why this is not cosmetic.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Cargo.toml").write_text(CARGO_FRESH)

    native_init._declare_cargo_metadata("Apache-2.0", "what this crate is")

    package = tomllib.loads((tmp_path / "Cargo.toml").read_text())["package"]
    assert package["description"] == "what this crate is"
    assert package["license"] == "Apache-2.0"


def test_the_cargo_fields_land_inside_the_package_table(native_init, tmp_path, monkeypatch):
    """Appending to the file would put them under [dependencies] and mean something else.

    tomllib reads a misplaced key without complaint, so the assertion has to be that the
    keys are in `package` rather than merely present somewhere in the document.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Cargo.toml").write_text(CARGO_FRESH)

    native_init._declare_cargo_metadata("MIT", "inside the table")

    parsed = tomllib.loads((tmp_path / "Cargo.toml").read_text())
    assert "description" in parsed["package"]
    assert "license" in parsed["package"]
    assert "description" not in parsed.get("dependencies", {})


def test_a_hostile_description_survives_both_manifest_formats(native_init, tmp_path, monkeypatch):
    """A quote and a backslash terminate or escape a value in both JSON and TOML."""
    monkeypatch.chdir(tmp_path)
    hostile = 'A probe: with a "quote" and a backslash \\ in it'
    (tmp_path / "package.json").write_text(BUN_FRESH)
    (tmp_path / "Cargo.toml").write_text(CARGO_FRESH)

    native_init._declare_npm_metadata("MIT", hostile)
    native_init._declare_cargo_metadata("MIT", hostile)

    assert json.loads((tmp_path / "package.json").read_text())["description"] == hostile
    cargo = tomllib.loads((tmp_path / "Cargo.toml").read_text())
    assert cargo["package"]["description"] == hostile


@pytest.mark.parametrize(
    ("helper", "manifest", "body"),
    [
        ("_declare_npm_metadata", "package.json", BUN_FRESH),
        ("_declare_cargo_metadata", "Cargo.toml", CARGO_FRESH),
    ],
)
def test_no_license_is_claimed_when_the_project_states_none(
    native_init, tmp_path, monkeypatch, helper, manifest, body
):
    """SPDX_ID=NONE writes no LICENSE file, so declaring one would be a false claim."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / manifest).write_text(body)

    getattr(native_init, helper)(native_init.NO_LICENSE, "still described")

    text = (tmp_path / manifest).read_text()
    assert "still described" in text
    assert not re.search(r'^\s*"?license"?\s*[:=]', text, re.MULTILINE)


@pytest.mark.parametrize(
    ("helper", "manifest", "body", "mine"),
    [
        (
            "_declare_npm_metadata",
            "package.json",
            '{\n  "name": "mine",\n  "description": "I wrote this",\n  "license": "MIT"\n}\n',
            "I wrote this",
        ),
        (
            "_declare_cargo_metadata",
            "Cargo.toml",
            '[package]\nname = "mine"\nversion = "0.1.0"\n'
            'description = "I wrote this"\nlicense = "MIT"\n',
            "I wrote this",
        ),
    ],
)
def test_metadata_the_manifest_already_carries_is_left_alone(
    native_init, tmp_path, monkeypatch, capsys, helper, manifest, body, mine
):
    """A description or license somebody wrote is theirs, and the report says nothing."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / manifest).write_text(body)

    getattr(native_init, helper)("Apache-2.0", "what the tool was given")

    text = (tmp_path / manifest).read_text()
    assert mine in text
    assert "Apache-2.0" not in text, "overwrote a license the manifest already declared"
    assert capsys.readouterr().out == "", "reported an edit it did not make"


def test_a_package_json_that_is_not_json_is_reported_not_rewritten(
    native_init, tmp_path, monkeypatch, capsys
):
    """Rewriting a manifest that is already broken would destroy whatever is in it."""
    monkeypatch.chdir(tmp_path)
    broken = '{\n  "name": "mine",\n'
    (tmp_path / "package.json").write_text(broken)

    native_init._declare_npm_metadata("MIT", "x")

    assert (tmp_path / "package.json").read_text() == broken
    assert "not valid JSON" in capsys.readouterr().out


def test_the_metadata_slot_is_split_on_the_first_colon_only():
    """A description may carry a colon; an SPDX id may not."""
    module = load_module(NATIVE_INIT, "native_init_split")
    assert module._split_metadata("MIT:a: b: c") == ("MIT", "a: b: c")
    assert module._split_metadata("MIT:") == ("MIT", "")
    assert module._split_metadata("") == ("", "")


@pytest.mark.parametrize(
    ("layer", "slot"),
    [
        ("lang-ts", "@@ SPDX_ID @@:@@ DESCRIPTION @@"),
        ("lang-rust", "@@ SPDX_ID @@:@@ DESCRIPTION @@"),
    ],
)
def test_each_language_task_is_handed_the_license_and_description(layer, slot):
    """A helper nothing calls with the right arguments does nothing.

    Every test above exercises the helpers directly, which passes whether or not the
    generated copier.yml actually hands them SPDX_ID and DESCRIPTION.
    """
    assert slot in (TEMPLATES / layer / "copier.yml").read_text(), (
        f"{layer} no longer receives the license and description"
    )
