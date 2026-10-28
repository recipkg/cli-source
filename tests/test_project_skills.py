"""Black-box lifecycle of project-owned skills with real Git repositories."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'src'


class ProjectSkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='recipkg-project-skills-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = os.environ.copy()
        self.env.update(PYTHONPATH=str(SOURCE), GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid',
                        GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid',
                        GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        self.origin = self.root / 'skill'
        self.project = self.root / 'project'
        self.origin.mkdir(); self.project.mkdir()
        self.git(self.origin, 'init', '-q')
        (self.origin / 'SKILL.md').write_text('---\nname: demo\ndescription: A project-owned skill.\n---\n\nFirst version.\n')
        (self.origin / 'script.py').write_text('print("do not run")\n')
        self.git(self.origin, 'add', '.')
        self.git(self.origin, 'commit', '-qm', 'first')
        self.first = self.git(self.origin, 'rev-parse', 'HEAD').stdout.strip()
        (self.origin / 'script.py').write_text('print("new version")\n')
        self.git(self.origin, 'commit', '-qam', 'second')
        self.second = self.git(self.origin, 'rev-parse', 'HEAD').stdout.strip()
        self.git(self.project, 'init', '-q')
        (self.project / 'README.md').write_text('Project\n')
        self.git(self.project, 'add', '.')
        self.git(self.project, 'commit', '-qm', 'initial')

    def git(self, cwd, *args):
        result = subprocess.run(['git', *args], cwd=cwd, env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def cli(self, *args, success=True):
        result = subprocess.run([sys.executable, '-m', 'recipkg', 'skills', 'project', *map(str, args),
                                 '--project', str(self.project)], env=self.env, cwd=self.root,
                                capture_output=True, text=True, timeout=30)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('Traceback', result.stderr)
        return result

    def test_add_list_update_and_preserve_modified_files(self):
        self.cli('add', self.origin, '--name', 'demo', '--rev', self.first)
        skill = self.project / '.agents/skills/demo'
        self.assertEqual((skill / 'script.py').read_text(), 'print("do not run")\n')
        self.assertIn('160000 ' + self.first, self.git(self.project, 'ls-files', '--stage', '.agents/skills/demo').stdout)
        self.assertIn('demo\t' + self.first, self.cli('list').stdout)
        self.git(self.project, 'commit', '-qm', 'add skill')
        self.cli('update', 'demo', '--rev', self.second)
        self.assertEqual((skill / 'script.py').read_text(), 'print("new version")\n')
        self.assertIn('160000 ' + self.second, self.git(self.project, 'ls-files', '--stage', '.agents/skills/demo').stdout)
        self.git(self.project, 'commit', '-qm', 'update skill')
        (skill / 'script.py').write_text('local edit\n')
        self.cli('update', 'demo', '--rev', self.first, success=False)
        self.assertEqual((skill / 'script.py').read_text(), 'local edit\n')

    def test_invalid_target_is_rejected_before_project_change(self):
        self.cli('add', self.origin, '--name', 'wrong-name', '--rev', self.first, success=False)
        self.cli('add', self.origin, '--name', 'demo', '--rev', 'main', success=False)
        self.assertFalse((self.project / '.gitmodules').exists())
        self.assertFalse((self.project / '.agents').exists())

    def test_ignored_skill_path_fails_before_cloning(self):
        (self.project / '.gitignore').write_text('.agents/\n')
        self.git(self.project, 'add', '.gitignore')
        self.git(self.project, 'commit', '-qm', 'ignore skills')
        result = self.cli('add', self.origin, '--name', 'demo', '--rev', self.first, success=False)
        self.assertIn('ignored by Git', result.stderr)
        self.assertFalse((self.project / '.agents').exists())
        self.assertFalse((self.project / '.gitmodules').exists())
        self.assertFalse((self.project / '.git/modules/recipkg-skill-demo').exists())

    def test_pinned_skill_does_not_require_remote_default_branch(self):
        bare = self.root / 'bare.git'
        self.git(self.root, 'clone', '--bare', str(self.origin), str(bare))
        self.git(bare, 'symbolic-ref', 'HEAD', 'refs/heads/unborn')
        self.cli('add', bare, '--name', 'demo', '--rev', self.first)
        skill = self.project / '.agents/skills/demo'
        self.assertTrue((skill / '.git').is_file())
        self.assertEqual((skill / 'script.py').read_text(), 'print("do not run")\n')
        self.assertIn('160000 ' + self.first, self.git(self.project, 'ls-files', '--stage', '.agents/skills/demo').stdout)
        self.assertEqual(self.git(skill, 'rev-parse', 'HEAD').stdout.strip(), self.first)
        self.git(self.project, 'commit', '-qm', 'add pinned skill')
        clone = self.root / 'clone'
        self.git(self.root, '-c', 'protocol.file.allow=always', 'clone', '--recurse-submodules',
                 str(self.project), str(clone))
        self.assertEqual((clone / '.agents/skills/demo/script.py').read_text(), 'print("do not run")\n')
        self.cli('update', 'demo', '--rev', self.second)
        self.assertEqual((skill / 'script.py').read_text(), 'print("new version")\n')

    @unittest.skipUnless(os.name == 'posix', 'test Git wrapper uses a POSIX shell')
    def test_add_failure_cleans_only_new_checkout(self):
        existing = self.project / '.agents/skills/existing'
        existing.mkdir(parents=True)
        (existing / 'note.txt').write_text('keep me\n')
        git = shutil.which('git')
        self.assertIsNotNone(git)
        tools = self.root / 'tools'
        tools.mkdir()
        wrapper = tools / 'git'
        wrapper.write_text('#!/bin/sh\ncase "$*" in *"submodule add"*) '
                           'echo "injected failure" >&2; exit 1;; esac\n'
                           f'exec "{git}" "$@"\n')
        wrapper.chmod(0o755)
        self.env['PATH'] = str(tools) + os.pathsep + self.env['PATH']
        result = self.cli('add', self.origin, '--name', 'demo', '--rev', self.first, success=False)
        self.assertIn('injected failure', result.stderr)
        self.assertFalse((self.project / '.agents/skills/demo').exists())
        self.assertEqual((existing / 'note.txt').read_text(), 'keep me\n')
        self.assertFalse((self.project / '.gitmodules').exists())

    @unittest.skipUnless(os.name == 'posix', 'test Git wrapper uses a POSIX shell')
    def test_registered_state_is_retained_if_git_reports_failure(self):
        git = shutil.which('git')
        self.assertIsNotNone(git)
        tools = self.root / 'tools'
        tools.mkdir()
        wrapper = tools / 'git'
        wrapper.write_text('#!/bin/sh\ncase "$*" in *"submodule add"*) '
                           f'"{git}" "$@" || exit $?; echo "injected failure" >&2; exit 1;; esac\n'
                           f'exec "{git}" "$@"\n')
        wrapper.chmod(0o755)
        self.env['PATH'] = str(tools) + os.pathsep + self.env['PATH']
        result = self.cli('add', self.origin, '--name', 'demo', '--rev', self.first, success=False)
        self.assertIn('partial skill state retained', result.stderr)
        self.assertTrue((self.project / '.gitmodules').is_file())
        self.assertEqual((self.project / '.agents/skills/demo/script.py').read_text(),
                         'print("do not run")\n')
        self.assertIn('160000 ' + self.first,
                      self.git(self.project, 'ls-files', '--stage', '.agents/skills/demo').stdout)

    def test_inherited_git_environment_cannot_redirect_project(self):
        other = self.root / 'other'
        other.mkdir()
        self.git(other, 'init', '-q')
        (other / 'README.md').write_text('other project\n')
        self.git(other, 'add', '.')
        self.git(other, 'commit', '-qm', 'initial')
        index = other / '.git/index'
        before = index.read_bytes()
        self.env.update(GIT_DIR=str(other / '.git'), GIT_WORK_TREE=str(other),
                        GIT_INDEX_FILE=str(index), GIT_CONFIG_COUNT='1',
                        GIT_CONFIG_KEY_0='invalid', GIT_CONFIG_VALUE_0='value')
        self.cli('add', self.origin, '--name', 'demo', '--rev', self.first)
        self.assertEqual(index.read_bytes(), before)
        self.assertEqual((self.project / '.agents/skills/demo/script.py').read_text(),
                         'print("do not run")\n')


if __name__ == '__main__':
    unittest.main()
