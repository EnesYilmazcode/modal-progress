from modal_progress.__main__ import main
from modal_progress.preflight import check, target


def app(tmp_path, body, *helpers):
    for name in helpers:
        (tmp_path / name).write_text("VALUE = 1\n")
    path = tmp_path / "app.py"
    path.write_text(body)
    return path


def test_names_a_local_module_that_is_not_added(tmp_path):
    path = app(tmp_path, "import modal\nimport json\nfrom helper import VALUE\n", "helper.py")
    [m] = check(path)
    assert (m.module, m.line) == ("helper", 3)
    assert m.message(path) == (
        "app.py:3 imports helper from helper.py, which Modal won't upload, so containers will fail "
        'with ModuleNotFoundError. Add .add_local_python_source("helper") to the image.'
    )


def test_a_local_package_counts_too(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "__init__.py").write_text("")
    [m] = check(app(tmp_path, "import modal\nimport tools.render\n"))
    assert m.module == "tools"


def test_quiet_when_the_image_adds_it(tmp_path):
    body = 'import modal\nfrom helper import VALUE\nimage = modal.Image.debian_slim().add_local_python_source("helper")\n'
    assert check(app(tmp_path, body, "helper.py")) == []


def test_quiet_when_the_folder_is_copied_to_root(tmp_path):
    body = 'import modal\nimport helper\nimage = modal.Image.debian_slim().add_local_dir(".", "/root")\n'
    assert check(app(tmp_path, body, "helper.py")) == []


def test_quiet_for_guarded_and_installed_imports(tmp_path):
    body = (
        "import modal\nimport numpy\n"
        "if modal.is_local():\n    import helper\n"
        "try:\n    import other\nexcept ImportError:\n    pass\n"
        "with modal.Image.debian_slim().imports():\n    import third\n"
    )
    assert check(app(tmp_path, body, "helper.py", "other.py", "third.py")) == []


def test_target_skips_flags_and_modules(tmp_path):
    path = app(tmp_path, "import modal\n")
    assert target(["--detach", f"{path}::main", "--n", "3"]) == path
    assert target(["-m", "pkg.app"]) is None


def test_check_command_exits_1_and_prints_each_problem(tmp_path, capsys):
    path = app(tmp_path, "import modal\nimport helper\n", "helper.py")
    assert main(["check", str(path)]) == 1
    assert "imports helper from helper.py" in capsys.readouterr().out
    assert main(["check", str(app(tmp_path, "import modal\n"))]) == 0


def test_skips_files_that_are_not_modal_apps(tmp_path):
    assert check(app(tmp_path, "import helper\n", "helper.py")) == []


def test_a_docstring_naming_the_module_does_not_count_as_adding_it(tmp_path):
    body = '"""Same timing as helper.py."""\nimport modal\nfrom helper import VALUE\n'
    assert [m.module for m in check(app(tmp_path, body, "helper.py"))] == ["helper"]
