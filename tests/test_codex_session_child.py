"""Child construction uses synthetic arguments, environments and temporary homes only."""

import json

import pytest

from claude_swap import codex_session as session
from claude_swap.exceptions import SessionError


STORES = ['-c', 'cli_auth_credentials_store="file"',
          '-c', 'mcp_oauth_credentials_store="file"']


@pytest.mark.parametrize('value', ['', None, 'private-parent'])
def test_inherited_home(value):
    env = {} if value is None else {'CODEX_HOME': value}
    if value:
        with pytest.raises(SessionError, match='CODEX_HOME'):
            session.refuse_inherited_home(env)
    else:
        session.refuse_inherited_home(env)


@pytest.mark.parametrize('name', session._CHILD_DROP)
@pytest.mark.parametrize('present', [False, True])
def test_environment_names(tmp_path, name, present):
    env = {'ORDINARY': 'unchanged', **({name: 'synthetic-secret'} if present else {})}
    before = env.copy()
    result, warnings = session.child_env(env, tmp_path)
    assert env == before
    assert result == {'ORDINARY': 'unchanged', 'CODEX_HOME': str(tmp_path)}
    assert warnings == ([f'Removed {name}.'] if present else [])


@pytest.mark.parametrize('args,exec_form', [
    ([], False), (['hello world'], False), (['resume', '--last'], False),
    (['fork', 'thread', 'prompt'], False), (['exec', 'prompt'], True),
    (['exec', 'resume', '--last', 'prompt'], True), (['e', 'resume', 'id'], True),
    (['-m', 'exec', 'resume'], False),
])
def test_supported_order(tmp_path, args, exec_form):
    original = args.copy()
    assert session.child_argv(args, tmp_path, config_layers=[]) == (
        ([] if exec_form else ['--no-daemon']) + STORES + args)
    assert args == original


@pytest.mark.parametrize('command', sorted(session._CHILD_COMMANDS))
def test_refused_commands(tmp_path, command):
    with pytest.raises(SessionError, match=command):
        session.child_argv(['--model=ordinary', command], tmp_path, config_layers=[])


@pytest.mark.parametrize('args', [
    ['exec', 'fork', 'id'], ['exec', 'review'], ['--profile', 'x'], ['-px'],
    ['--profile=x'], ['-p', 'x'], ['--oss'], ['--local-provider=ollama'],
    ['--remote', 'endpoint'], ['resume', '--remote=endpoint'],
    ['--remote-auth-token-env=X'], ['--unknown'], ['-c'], ['-c', 'missing-equals'],
    ['--model'], ['-c', '=value'],
])
def test_refused_options(tmp_path, args):
    with pytest.raises(SessionError):
        session.child_argv(args, tmp_path, config_layers=[])


@pytest.mark.parametrize('key', sorted(session._CHILD_FORBIDDEN))
@pytest.mark.parametrize('form', ['short', 'long', 'equal', 'attached'])
def test_protected_overrides(tmp_path, key, form):
    raw = key + '.nested={ child = "synthetic-secret" }'
    args = {'short': ['-c', raw], 'long': ['--config', raw],
            'equal': ['--config=' + raw], 'attached': ['-c' + raw]}[form]
    with pytest.raises(SessionError) as error:
        session.child_argv(['exec', 'resume', '--last'] + args, tmp_path, config_layers=[])
    assert key in str(error.value)
    assert 'synthetic-secret' not in str(error.value)


@pytest.mark.parametrize('raw', [
    'model_providers={ custom = { base_url = "synthetic-secret" } }',
    'cli_auth_credentials_store="file"', 'mcp_oauth_credentials_store=keyring',
])
def test_parent_table_and_store_conflicts(tmp_path, raw):
    with pytest.raises(SessionError):
        session.child_argv(['-c', raw], tmp_path, config_layers=[])


@pytest.mark.parametrize('raw,expected', [
    ('model="quoted=value"', ('model', 'quoted=value')),
    ('model=raw words', ('model', 'raw words')),
    ('model=\'broken"', ('model', 'broken')),
    ('other={ nested = true }', ('other', {'nested': True})),
    (' model = 42 ', ('model', 42)),
])
def test_codex_toml_fallback(raw, expected):
    assert session._child_override(raw) == expected


@pytest.mark.parametrize('args', [
    ['--', '--profile', 'login', '-c', 'model_provider=secret'],
    ['--model', 'login', '--sandbox=workspace-write', '-aon-request', '-C', 'exec',
     '--add-dir=resume', '--search', 'ordinary prompt'],
    ['--image', 'login.png', 'exec', 'resume', '--search'],
    ['exec', 'resume', '--image', 'image.png', '--', '--remote'],
])
def test_ordinary_arguments_unchanged(tmp_path, args):
    result = session.child_argv(args, tmp_path, config_layers=[])
    assert result[-len(args):] == args


@pytest.mark.parametrize('private', [False, True])
def test_sqlite_env(tmp_path, private):
    home = tmp_path / 'home'
    value = str(home / 'db' if private else tmp_path / 'outside')
    result, warnings = session.child_env({'CODEX_SQLITE_HOME': value}, home)
    assert result.get('CODEX_SQLITE_HOME') == (value if private else None)
    assert warnings == ([] if private else ['Removed CODEX_SQLITE_HOME.'])


@pytest.mark.parametrize('key', ['sqlite_home', 'log_dir'])
@pytest.mark.parametrize('managed', [False, True])
@pytest.mark.parametrize('private', [False, True])
def test_config_paths(tmp_path, key, managed, private):
    home = tmp_path / 'home'
    value = str(home / 'private' if private else tmp_path / 'escape')
    layers = [(tmp_path / 'config.toml', {key: value}, managed)]
    if managed and not private:
        with pytest.raises(SessionError, match=key):
            session.child_argv([], home, config_layers=layers)
        return
    result = session.child_argv([], home, config_layers=layers)
    default = home if key == 'sqlite_home' else home / 'log'
    expected = [] if private else ['-c', key + '=' + json.dumps(str(default))]
    assert result == ['--no-daemon'] + STORES + expected


@pytest.mark.parametrize('key', ['sqlite_home', 'log_dir'])
def test_last_override_and_delimiter(tmp_path, key):
    home = tmp_path / 'home'
    private = f'{key}={json.dumps(str(home / "private"))}'
    escape = f'{key}={json.dumps(str(tmp_path / "escape"))}'
    args = ['exec', 'resume', '--last', '-c', escape, '--config=' + private, '--', 'prompt']
    assert session.child_argv(args, home, config_layers=[]) == STORES + args
    args = ['exec', '-c', private, '--config=' + escape, '--', 'prompt']
    result = session.child_argv(args, home, config_layers=[])
    default = home if key == 'sqlite_home' else home / 'log'
    assert result == STORES + args[:-2] + ['-c', key + '=' + json.dumps(str(default))] + args[-2:]


def test_relative_config_layer_and_precedence(tmp_path):
    home = tmp_path / 'home'
    layers = [(tmp_path / 'config.toml', {'sqlite_home': 'home/db'}, False),
              (home / 'config.toml', {'log_dir': 'logs'}, False)]
    assert session.child_argv([], home, config_layers=layers) == ['--no-daemon'] + STORES
    layers.append((tmp_path / 'config.toml', {'sqlite_home': 'outside'}, False))
    assert 'sqlite_home=' in session.child_argv([], home, config_layers=layers)[-1]


def test_symlink_escape(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (home / 'link').symlink_to(outside, target_is_directory=True)
    value = str(home / 'link' / 'missing' / 'db')
    result, warnings = session.child_env({'CODEX_SQLITE_HOME': value}, home)
    assert 'CODEX_SQLITE_HOME' not in result
    assert warnings == ['Removed CODEX_SQLITE_HOME.']
    layers = [(home / 'config.toml', {'sqlite_home': value}, False)]
    assert session.child_argv([], home, config_layers=layers)[-1] == 'sqlite_home=' + json.dumps(str(home))
    with pytest.raises(SessionError):
        session.child_argv([], home, config_layers=[(home / 'config.toml', {'log_dir': value}, True)])


@pytest.mark.parametrize('raw', ['sqlite_home={x=1}', 'log_dir=false', 'sqlite_home.child="x"'])
def test_invalid_path_override(tmp_path, raw):
    with pytest.raises(SessionError):
        session.child_argv(['-c', raw], tmp_path, config_layers=[])


def test_path_resolution_failure(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError('synthetic-secret')
    monkeypatch.setattr(type(tmp_path), 'resolve', denied)
    with pytest.raises(SessionError, match='Cannot resolve') as error:
        session.child_env({'CODEX_SQLITE_HOME': str(tmp_path)}, tmp_path)
    assert 'synthetic-secret' not in str(error.value)


def test_relative_override_uses_child_cd(tmp_path):
    home = tmp_path / 'home'
    args = ['--cd', str(home), '-c', 'sqlite_home="db"', 'exec', 'prompt']
    assert session.child_argv(args, home, config_layers=[]) == STORES + args
    args = ['--cd', str(tmp_path), '-c', 'sqlite_home="db"', 'resume', '--last']
    result = session.child_argv(args, home, config_layers=[])
    assert result[-1] == 'sqlite_home=' + json.dumps(str(home))


def test_relative_sqlite_env_pinned_before_cd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    home = tmp_path / 'home'
    result, warnings = session.child_env({'CODEX_SQLITE_HOME': ' home/db '}, home)
    assert result['CODEX_SQLITE_HOME'] == str(home / 'db')
    assert warnings == []


@pytest.mark.parametrize('value', ['', '   ', ' /outside '])
def test_empty_or_trimmed_sqlite_env(tmp_path, value):
    result, warnings = session.child_env({'CODEX_SQLITE_HOME': value}, tmp_path)
    assert 'CODEX_SQLITE_HOME' not in result
    assert warnings == ['Removed CODEX_SQLITE_HOME.']


@pytest.mark.parametrize('where', ['env', 'config', 'override', 'cd'])
def test_home_expansion_refused_without_home_lookup(tmp_path, where):
    with pytest.raises(SessionError, match='Home expansion'):
        if where == 'env':
            session.child_env({'CODEX_SQLITE_HOME': '~/db'}, tmp_path)
        else:
            args = {'config': [], 'override': ['-c', 'sqlite_home="~/db"'],
                    'cd': ['--cd=~/project']}[where]
            layers = [(tmp_path / 'config.toml', {'sqlite_home': '~/db'}, False)] if where == 'config' else []
            session.child_argv(args, tmp_path, config_layers=layers)


def test_default_log_symlink_escape_refused(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (home / 'log').symlink_to(outside, target_is_directory=True)
    with pytest.raises(SessionError, match='log_dir'):
        session.child_argv([], home, config_layers=[])


@pytest.mark.parametrize('args', [['prompt', 'login'], ['exec', 'prompt', 'review']])
def test_ambiguous_subcommand_refused(tmp_path, args):
    with pytest.raises(SessionError, match=args[-1]):
        session.child_argv(args, tmp_path, config_layers=[])


def test_managed_escape_not_hidden_by_runtime_override(tmp_path):
    layers = [(tmp_path / 'config.toml', {'sqlite_home': '/outside'}, True)]
    with pytest.raises(SessionError, match='Managed'):
        session.child_argv(['-c', 'sqlite_home=' + json.dumps(str(tmp_path))], tmp_path, config_layers=layers)


def test_symlink_loop_refused(tmp_path):
    (tmp_path / 'loop').symlink_to(tmp_path / 'loop')
    with pytest.raises(SessionError, match='Cannot resolve'):
        session.child_env({'CODEX_SQLITE_HOME': str(tmp_path / 'loop')}, tmp_path)


@pytest.mark.parametrize('option', ['--image=file.png', '-ifile.png'])
def test_attached_variadic_images_consume_command_words(tmp_path, option):
    args = [option, 'exec', 'login', '--search']
    assert session.child_argv(args, tmp_path, config_layers=[]) == ['--no-daemon'] + STORES + args


def test_unused_default_log_escape_does_not_replace_private_config(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'log').symlink_to(tmp_path / 'outside', target_is_directory=True)
    layers = [(home / 'config.toml', {'log_dir': str(home / 'private-log')}, False)]
    assert session.child_argv([], home, config_layers=layers) == ['--no-daemon'] + STORES
