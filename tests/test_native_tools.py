"""Tests for what a native tool leaves behind, and for the tasks that invoke one.

Every case here is something a native tool wrote that a layer owns, or a script a
layer shipped without wiring. Both are junk in a fresh repository; one was data loss.
"""

from __future__ import annotations

import importlib.util
import json
import os
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
    recipe = (TEMPLATES / "lang-go/.just.d/go.just").read_text().split("go-fmt:\n", 1)[1]
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
    with pytest.raises(SystemExit):
        script.destination(root, "../escape")
    with pytest.raises(SystemExit):
        script.destination(root, os.sep + "absolute")
    assert script.destination(root, "infrastructure") == root / "infrastructure"


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
