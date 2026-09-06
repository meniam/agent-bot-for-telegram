# `pip-audit` из `uvx` падает на macOS, пока сам создаёт venv для резолва

Found: 2026-09-06
Plan: `.agents/plans/20260906_1300_tooling_follows_python_and_ruff_rules.md`
Current: yes

## Expected

Рецепт из `~/.agents/rules/python.md` — `uv export --locked … | uvx pip-audit -r` —
работает на любой машине с uv.

## Actual

`uvx pip-audit -r file` создаёт временный venv через `venv.EnvBuilder(with_pip=True)`
без symlinks: бинарь uv-managed Python копируется, а он слинкован с
`@rpath/libpython3.14.dylib`, которого рядом с копией нет. `ensurepip` внутри
этого venv умирает с `SIGABRT` («dyld: Library not loaded»). То же на 3.13.
`python -m venv` из того же интерпретатора напрямую работает: он ставит symlink.
Песочница Claude Code ни при чём, без неё падает так же.

## Verified

macOS 25.6, uv 0.11.8, cpython-3.14.4-macos-aarch64-none:

```sh
uvx --from pip-audit python -c "import venv; venv.EnvBuilder(with_pip=True).create('/tmp/v')"
# subprocess.CalledProcessError: ... ensurepip ... died with <Signals.SIGABRT: 6>
/tmp/v/bin/python3.14 -m ensurepip   # dyld[...]: Library not loaded: @rpath/libpython3.14.dylib
```

Обход: `uvx pip-audit --disable-pip -r file` — экспорт полностью запинен и с хэшами,
резолвер и venv не нужны. Через `UV_PYTHON_PREFERENCE=only-system` тоже работает,
но зависит от Homebrew-питона на машине.

## Do

`just audit` вызывает `pip-audit --disable-pip`; не убирать флаг, пока uv-managed
Python на macOS линкуется с libpython динамически.
