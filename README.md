# Recipkg reference CLI

This repository contains the replaceable Python reference implementation of [Recipkg](https://github.com/recipkg/recipkg). Requirements, recipe specifications, public skill sources, and examples are maintained in the canonical recipe repository. This code is a replaceable implementation of that contract.

Clone recursively with `git clone --recurse-submodules https://github.com/recipkg/cli-source.git`, or initialize existing checkouts using `git submodule update --init --recursive`. Packaged public skills are child submodules from their own canonical repositories.

Python 3.11+ is required; runtime dependencies are standard-library modules only. Git is needed to fetch pinned Git recipe sources.

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install .
recipkg --help
recipkg search --limit 10
recipkg extract /path/to/software --lang en
recipkg extract /path/to/software --lang zh-CN
recipkg skills list
```

The five action commands print prompts, without calling a model or executing software. `search` explicitly queries public GitHub repositories with the `recipkg` topic; candidates still need recipe validation. Prompt language changes the task template, never the original recipe material. Public skills include English and Simplified Chinese instructions.

`skills project add`, `list`, and `update` manage full project skills as pinned Git submodules, including scripts and other resources. They stage changes for review without committing or executing the skills. Specify a full commit and a Git project root; local modifications and conflicting paths are preserved.

## Local verification

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

For integration acceptance against the canonical recipe, set `RECIPKG_RECIPE_ROOT` to its checkout. Without that checkout, the recipe-resource integration test is explicitly skipped; the remaining tests are self-contained and offline.

```sh
RECIPKG_RECIPE_ROOT=/path/to/recipkg PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Do not hand-maintain competing template/skill requirements here: update `recipes/recipkg/` in the canonical repository, then refresh the pinned skill submodules under `src/recipkg/data/skills/` and packaged template resources. See the [English guide](https://github.com/recipkg/recipkg/blob/main/README.en.md) and [specification](https://github.com/recipkg/recipkg/blob/main/docs/SPEC.en.md).

## 中文

这是 Recipkg 的可替换 Python 参考实现，存放在独立的 `recipkg/cli-source` 仓库，在主仓库的 `generated/cli-source/` 以固定提交的子模块引用。需求、规范、公开技能和示例以 [主仓库](https://github.com/recipkg/recipkg) 为准。修改配方后同步打包资源，并在本机运行测试；测试通过不等于独立重建或所有平台验收通过。

`skills project add`、`list`、`update` 以固定提交的 Git 子模块管理项目完整技能，包括脚本和资源；改动会暂存以供审查，不会自动提交或运行技能。请指定完整提交及 Git 项目根目录，已有本地修改与冲突路径会保留。
