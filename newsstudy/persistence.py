"""Publish only allowlisted, credential-free artifacts; fail closed on races."""
import os
from pathlib import Path
import re
import subprocess


class PersistenceError(RuntimeError):
    pass


class GitPersistence:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.branch = 'main'

    def git(self, *args, check=True):
        try:
            proc = subprocess.run(['git', *args], cwd=self.root, capture_output=True,
                                  timeout=120, env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
        except (OSError, subprocess.TimeoutExpired):
            raise PersistenceError('Git 同步不可用；已停止发送，请检查远端和网络') from None
        if check and proc.returncode:
            # Never relay stderr: authentication URLs and remote responses can contain secrets.
            raise PersistenceError('Git 同步失败；已停止发送。请检查推送保护、权限或远端并发更新')
        return proc

    def remote_head(self):
        lines = self.git('ls-remote', '--exit-code', 'origin', 'refs/heads/' + self.branch).stdout.split()
        if not lines:
            raise PersistenceError('无法确认远端状态；已停止发送')
        return lines[0].decode('ascii')

    def prepare(self, state):
        # Workflow checkout pins main and fetches complete history. Never merge SQLite,
        # overwrite another run's ledger, force push, or switch a user's branch.
        branch = self.git('branch', '--show-current').stdout.decode().strip()
        head = self.git('rev-parse', 'HEAD').stdout.decode().strip()
        if branch != self.branch or self.remote_head() != head:
            raise PersistenceError('本地不是最新 main；请重新运行工作流，未发送微信')
        state.restore_public()
        self(state)  # Migrate legacy SQLite out of Git BEFORE any external message.

    @staticmethod
    def allowed(path):
        return (path == 'data/delivery-state.json' or
                re.fullmatch(r'data/\d{4}-\d{2}-\d{2}/(source|speech-candidates)\.json', path) or
                re.fullmatch(r'output/pdf/\d{4}-\d{2}-\d{2}/(analysis\.json|source\.json|report\.pdf)', path) or
                re.fullmatch(r'public/pdf/\d{4}-\d{2}-\d{2}\.pdf', path))

    def __call__(self, state):
        state.export_public()
        # Refuse to publish unrelated staged user changes.
        staged = self.git('diff', '--cached', '--name-only', '-z').stdout.decode().split('\0')
        if any(p and not self.allowed(p) and p != 'data/state.sqlite3' for p in staged):
            raise PersistenceError('暂存区含非日报文件；已停止同步和发送')
        paths = []
        for folder in ('data', 'output', 'public'):
            base = self.root / folder
            if base.exists():
                paths.extend(p for p in base.rglob('*') if p.is_file() and self.allowed(p.relative_to(self.root).as_posix()))
        secret_values = [v.encode() for k, v in os.environ.items()
                         if k.startswith('NEWSSTUDY_') and ('KEY' in k or 'SECRET' in k or 'TOKEN' in k or 'PAT' in k)
                         and len(v) >= 8]
        sendkeys = os.environ.get('NEWSSTUDY_SERVERCHAN_SENDKEY', '').split(',')
        secret_values.extend(k.strip().encode() for k in sendkeys if len(k.strip()) >= 8)
        for path in paths:
            if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                raise PersistenceError('日报产物不是工作区内的普通文件；已停止提交和发送')
            data = path.read_bytes()
            if (any(v in data for v in secret_values) or b'q-sign-algorithm=' in data or
                    re.search(rb'AKID[A-Za-z0-9]{16,}', data)):
                raise PersistenceError('日报产物含敏感内容；已停止提交和发送（不显示内容）')
        if paths:
            self.git('add', '-f', '--', *(p.relative_to(self.root).as_posix() for p in paths))
        self.git('rm', '--cached', '--ignore-unmatch', '--', 'data/state.sqlite3',
                 'data/state.sqlite3-wal', 'data/state.sqlite3-shm', 'data/run.lock')
        # Detect a stale base BEFORE committing. A moved remote is a new run, not a merge task.
        head = self.git('rev-parse', 'HEAD').stdout.decode().strip()
        if self.remote_head() != head:
            raise PersistenceError('远端已更新；请从最新 main 重跑，未继续发送')
        if self.git('diff', '--cached', '--quiet', check=False).returncode:
            self.git('commit', '-m', 'daily: persist safe delivery state')
        head = self.git('rev-parse', 'HEAD').stdout.decode().strip()
        self.git('push', 'origin', 'HEAD:refs/heads/' + self.branch)
        # Even a successful push must be observable before the caller may send.
        if self.remote_head() != head:
            raise PersistenceError('无法确认远端已保存；已停止发送，需核对投递状态')
