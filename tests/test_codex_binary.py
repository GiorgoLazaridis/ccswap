"""Binary discovery and probes use synthetic installations and empty homes."""

import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from claude_swap import codex_binary as binary
from claude_swap.exceptions import SessionError


def touch(path, content=''):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def metadata(root, name='@openai/codex', version='0.164.0'):
    touch(root / 'package.json', json.dumps({'name': name, 'version': version}))


@pytest.fixture
def installation(tmp_path, monkeypatch):
    shim = touch(tmp_path / 'npm' / 'codex.cmd')
    package = shim.parent / 'node_modules' / '@openai' / 'codex'
    metadata(package)
    touch(package / 'bin' / 'codex.js')
    monkeypatch.setattr(binary, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(binary.shutil, 'which', lambda name, path: str(shim))
    return shim, package


def platform(package, arch='x64', where='hoisted', executable=True):
    root = (package.parent if where == 'hoisted' else
            package / ('bin/node_modules/@openai' if where == 'bin' else 'node_modules/@openai'))
    root = root / f'codex-win32-{arch}'
    metadata(root, '@openai/codex', f'0.164.0-win32-{arch}')
    exe = root / 'vendor' / binary._TARGETS[arch] / 'bin' / 'codex.exe'
    if executable:
        touch(exe)
    return exe


def resolve(**env):
    return binary.resolve_codex_binary({'PATH': 'synthetic', 'PROCESSOR_ARCHITECTURE': 'AMD64', **env})


@pytest.mark.parametrize('where', ['hoisted', 'nested', 'bin'])
def test_node_search(installation, where):
    _, package = installation
    expected = platform(package, where=where)
    if where != 'hoisted':
        platform(package, where='hoisted')
    assert resolve() == [str(expected)]


def test_legacy(installation):
    _, package = installation
    expected = touch(package / 'vendor' / binary._TARGETS['x64'] / 'bin' / 'codex.exe')
    assert resolve() == [str(expected)]
    platform(package, executable=False)
    with pytest.raises(SessionError, match='Put the native codex.exe on PATH'):
        resolve()


@pytest.mark.parametrize('arch,wow', [('ARM64', ''), ('AMD64', 'ARM64'), ('x86', 'ARM64')])
def test_os_arm64(installation, arch, wow):
    _, package = installation
    expected = platform(package, 'arm64')
    assert resolve(PROCESSOR_ARCHITECTURE=arch, PROCESSOR_ARCHITEW6432=wow) == [str(expected)]
    platform(package, 'x64')
    with pytest.raises(SessionError):
        resolve(PROCESSOR_ARCHITECTURE=arch, PROCESSOR_ARCHITEW6432=wow)


def test_emulated_x64_only(installation):
    expected = platform(installation[1], 'x64')
    assert resolve(PROCESSOR_ARCHITECTURE='ARM64') == [str(expected)]
    assert resolve(PROCESSOR_ARCHITECTURE='x86', PROCESSOR_ARCHITEW6432='AMD64') == [str(expected)]


@pytest.mark.parametrize('name,version', [('other', '0.164.0'), ('@openai/codex', '0.163.0')])
def test_wrong_metadata(installation, name, version):
    metadata(installation[1], name, version)
    with pytest.raises(SessionError):
        resolve()


@pytest.mark.parametrize('failure', ['layout', 'missing-js', 'no-binary', 'bad-json', 'arch', 'denied'])
def test_unsupported(installation, monkeypatch, failure):
    shim, package = installation
    if failure == 'layout':
        monkeypatch.setattr(binary.shutil, 'which', lambda *a, **kw: str(touch(shim.with_suffix('.js'))))
    elif failure == 'missing-js':
        (package / 'bin/codex.js').unlink()
    elif failure == 'bad-json':
        (package / 'package.json').write_text('{')
    elif failure == 'denied':
        monkeypatch.setattr(binary, '_metadata', lambda *a: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(SessionError):
        resolve(**({'PROCESSOR_ARCHITECTURE': 'unknown'} if failure == 'arch' else {}))


@pytest.mark.parametrize('windows', [False, True])
def test_direct(tmp_path, monkeypatch, windows):
    executable = touch(tmp_path / ('codex.exe' if windows else 'codex'))
    seen = []
    monkeypatch.setattr(binary, 'os', SimpleNamespace(name='nt' if windows else 'posix'))
    monkeypatch.setattr(binary.shutil, 'which', lambda name, path: seen.append((name, path)) or str(executable))
    assert resolve() == [str(executable)]
    assert seen == [('codex', 'synthetic')]


def test_missing(monkeypatch):
    monkeypatch.setattr(binary.shutil, 'which', lambda *a, **kw: None)
    with pytest.raises(SessionError):
        resolve()


class Probe:
    def __init__(self, output, result=0, timeout=False):
        self.stdout = io.BytesIO(output)
        self.result = result
        self.timeout = timeout
        self.killed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stdout.close()

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired('synthetic', timeout)
        return self.result


def fake_probe(monkeypatch, outputs, **options):
    calls = []
    def popen(argv, **kwargs):
        home = Path(kwargs['env']['CODEX_HOME'])
        calls.append((argv, kwargs, home, list(home.iterdir())))
        return Probe(outputs[len(calls) - 1], **options)
    monkeypatch.setattr(binary.subprocess, 'Popen', popen)
    return calls


def test_probe_isolation(monkeypatch):
    inherited = {'PATH': 'synthetic', 'HOME': '/real', 'USERPROFILE': '/real',
                 'CODEX_HOME': '/real', 'TOKEN': 'synthetic-secret', 'NODE_OPTIONS': 'bad',
                 'SYSTEMROOT': 'win', 'TEMP': 'temp'}
    monkeypatch.setattr(binary, 'os', SimpleNamespace(environ=inherited))
    calls = fake_probe(monkeypatch, [b'codex-cli 0.164.0\n', b'Usage:\n  --no-daemon\n'])
    binary.probe_codex(['/native path/codex'])
    assert len(calls) == 2
    for argv, kwargs, home, contents in calls:
        assert argv[0] == '/native path/codex'
        assert contents == []
        assert kwargs == dict(env={'PATH': 'synthetic', 'SYSTEMROOT': 'win', 'TEMP': 'temp',
                                   'HOME': str(home), 'USERPROFILE': str(home), 'CODEX_HOME': str(home)},
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, shell=False, bufsize=0)
        assert not home.exists()
    assert calls[0][0][-1] == '--version'
    assert calls[1][0][-1] == '--help'
    assert inherited['HOME'] == '/real'


@pytest.mark.parametrize('output', [b'codex-cli 0.163.0', b'codex-cli 0.164.00',
                                    b'0.164.0', b'noise\ncodex-cli 0.164.0', b'\xff'])
def test_version_refused(monkeypatch, output):
    calls = fake_probe(monkeypatch, [output])
    with pytest.raises(SessionError, match='requires Codex 0.164.0'):
        binary.probe_codex(['synthetic'])
    assert not calls[0][2].exists()


@pytest.mark.parametrize('help_output', [b'help', b'--no-daemon-extra', b'x--no-daemon'])
def test_capability_refused(monkeypatch, help_output):
    fake_probe(monkeypatch, [b'codex-cli 0.164.0', help_output])
    with pytest.raises(SessionError, match='--no-daemon'):
        binary.probe_codex(['synthetic'])


@pytest.mark.parametrize('failure', ['timeout', 'exit', 'oversized', 'spawn'])
def test_probe_failure(monkeypatch, failure):
    if failure == 'spawn':
        monkeypatch.setattr(binary.subprocess, 'Popen', lambda *a, **kw: (_ for _ in ()).throw(OSError('synthetic-secret')))
    else:
        calls = fake_probe(monkeypatch, [b'x' * (binary._LIMIT + 1) if failure == 'oversized' else b''],
                           timeout=failure == 'timeout', result=1 if failure == 'exit' else 0)
    with pytest.raises(SessionError) as error:
        binary.probe_codex(['synthetic'])
    assert 'synthetic-secret' not in str(error.value)
    if failure != 'spawn':
        assert not calls[0][2].exists()


@pytest.mark.parametrize('suffix', ['.cmd', '.ps1', '.bat'])
def test_shim_extensions(installation, monkeypatch, suffix):
    shim, package = installation
    expected = platform(package)
    shim = touch(shim.with_suffix(suffix))
    monkeypatch.setattr(binary.shutil, 'which', lambda *a, **kw: str(shim))
    assert resolve() == [str(expected)]


@pytest.mark.parametrize('name,version', [('wrong', '0.164.0'), ('@openai/codex', '0.165.0')])
def test_metadata_failures_keep_diagnostics_private(installation, name, version):
    metadata(installation[1], name, version)
    with pytest.raises(SessionError) as error:
        resolve()
    assert name not in str(error.value)
    assert version not in str(error.value)


@pytest.mark.parametrize('scenario', ['ok', 'oversized', 'timeout', 'exit'])
def test_real_probe_with_synthetic_program(tmp_path, monkeypatch, scenario):
    import sys

    script = touch(tmp_path / 'synthetic_probe.py', '''
import os, sys, time
from pathlib import Path
home = Path(os.environ['CODEX_HOME'])
assert home == Path(os.environ['HOME']) == Path(os.environ['USERPROFILE'])
assert list(home.iterdir()) == []
assert not {'OPENAI_API_KEY', 'NODE_OPTIONS', 'SYNTHETIC_SECRET'} & os.environ.keys()
scenario = sys.argv[1]
if scenario == 'timeout':
    time.sleep(10)
if scenario == 'exit':
    sys.exit(3)
if scenario == 'oversized':
    print('x' * 200000)
else:
    print('codex-cli 0.164.0' if sys.argv[-1] == '--version' else '--no-daemon')
''')
    monkeypatch.setenv('SYNTHETIC_SECRET', 'synthetic')
    monkeypatch.setenv('NODE_OPTIONS', 'synthetic')
    monkeypatch.setattr(binary, '_TIMEOUT', 0.5)
    if scenario == 'ok':
        binary.probe_codex([sys.executable, str(script), scenario])
    else:
        with pytest.raises(SessionError):
            binary.probe_codex([sys.executable, str(script), scenario])


@pytest.mark.parametrize('env', [
    {'PROCESSOR_ARCHITECTURE': 'AMD64', 'PROCESSOR_ARCHITEW6432': 'unknown'},
    {'PROCESSOR_ARCHITECTURE': 'ARM64', 'PROCESSOR_ARCHITEW6432': 'AMD64'},
    {'PROCESSOR_ARCHITECTURE': 'x86'},
])
def test_uncertain_architecture(installation, env):
    platform(installation[1])
    with pytest.raises(SessionError):
        resolve(**env)


def test_oversized_help(monkeypatch):
    fake_probe(monkeypatch, [b'codex-cli 0.164.0', b'--no-daemon\n' + b'x' * binary._LIMIT])
    with pytest.raises(SessionError):
        binary.probe_codex(['synthetic'])


@pytest.mark.parametrize('prefix', [[], [''], [None]])
def test_invalid_prefix(prefix):
    with pytest.raises(SessionError):
        binary.probe_codex(prefix)


def test_probe_ignores_stderr_warnings(monkeypatch):
    # Real Codex 0.164.0 warns on stderr about helper aliases in a temporary home.
    seen = []

    class Fake:
        def __init__(self, argv, **kwargs):
            seen.append(kwargs["stderr"])
            text = b"codex-cli 0.164.0\n" if argv[-1] == "--version" else b"  --no-daemon\n"
            self.stdout = io.BytesIO(text)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(binary.subprocess, "Popen", Fake)
    binary.probe_codex(["codex"])
    assert seen == [binary.subprocess.DEVNULL] * 2
