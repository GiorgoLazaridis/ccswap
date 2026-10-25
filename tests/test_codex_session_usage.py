"""Session usage authority with synthetic homes and credentials only."""
import copy

import pytest

from claude_swap import codex as module, codex_session as cs
from claude_swap.codex_usage import CodexUsageError
from claude_swap.locking import FileLock
from tests.test_codex_reconcile import custody, write


@pytest.fixture
def usage(custody, monkeypatch):
    switcher, home, baseline = custody
    switcher.lock_file = switcher.backup_dir / '.lock'
    switcher.auth_file = switcher.codex_home / 'auth.json'
    switcher._usage_cache.clear()
    monkeypatch.setattr(switcher, 'current_account_number', lambda: None)
    monkeypatch.setattr(cs, 'slot_custody', lambda *_: cs.Custody('live', 'writers_present'))
    return switcher, home, baseline


@pytest.mark.parametrize('entrypoint', ['accounts_snapshot', 'usage_status'])
@pytest.mark.parametrize('unauthorized', [False, True])
def test_live_rotated_read_only(usage, monkeypatch, entrypoint, unauthorized):
    switcher, home, baseline = usage
    rotated = copy.deepcopy(baseline)
    rotated['tokens']['access_token'] = 'synthetic-rotated'
    write(home / 'auth.json', rotated)
    calls = []

    def fetch(auth, **kwargs):
        calls.append(auth)
        if unauthorized:
            raise CodexUsageError('unauthorized', status_code=401)
        return {'five_hour': {'pct': 10}}

    monkeypatch.setattr(module, 'fetch_codex_usage', fetch)
    monkeypatch.setattr(switcher, '_refresh_inactive_auth', lambda *_: pytest.fail('refresh'))
    result = getattr(switcher, entrypoint)()
    entry = switcher._usage_cache['1']
    assert calls == [rotated]
    assert bool(entry.last_error) == unauthorized
    if entrypoint == 'accounts_snapshot':
        assert not result.accounts[0].switchable


@pytest.mark.parametrize('state', ['uncertain', 'conflict'])
def test_unavailable_never_uses_backup(usage, monkeypatch, state):
    switcher, _, _ = usage
    monkeypatch.setattr(cs, 'slot_custody', lambda *_: cs.Custody(state, 'fixed_reason'))
    monkeypatch.setattr(module, 'fetch_codex_usage', lambda *_args, **_kw: pytest.fail('fetch'))
    snapshot = switcher.accounts_snapshot()
    assert snapshot.accounts[0].usage.last_error == 'fixed_reason'
    assert not snapshot.accounts[0].switchable


@pytest.mark.parametrize('failures', [2, 3])
def test_bounded_strict_read(usage, monkeypatch, failures):
    switcher, home, _ = usage
    original = cs.load_document
    calls = []

    def read(path):
        if path == home / 'auth.json':
            calls.append(path)
            if len(calls) <= failures:
                from claude_swap.exceptions import SessionError
                raise SessionError('Malformed session record.')
        return original(path)

    monkeypatch.setattr(cs, 'load_document', read)
    with FileLock(switcher.lock_file):
        effective = switcher._effective_account_auth('1', allow_refresh=True)
    assert len(calls) == 3
    assert (effective['auth'] is None) == (failures == 3)


def test_authority_change_invalidates_cache(usage, monkeypatch):
    switcher, home, baseline = usage
    seen = []
    monkeypatch.setattr(module, 'fetch_codex_usage', lambda auth, **_: seen.append(auth) or {})
    switcher.accounts_snapshot()
    rotated = dict(baseline, rotation=1)
    write(home / 'auth.json', rotated)
    switcher.accounts_snapshot()
    assert seen == [baseline, rotated]


def test_idle_adopts_before_refresh(usage, monkeypatch):
    switcher, home, baseline = usage
    rotated = dict(baseline, rotation=1)
    write(home / 'auth.json', rotated)
    monkeypatch.setattr(cs, 'slot_custody', lambda *_: cs.Custody('quiescent', 'no_writers'))
    calls = []

    def fetch(auth, **_):
        calls.append(auth)
        if len(calls) == 1:
            raise CodexUsageError('unauthorized', status_code=401)
        return {}

    def refresh(number, expected):
        assert expected == rotated == cs.load_document(switcher._credential_path(number))
        return dict(expected, refreshed=True)

    monkeypatch.setattr(module, 'fetch_codex_usage', fetch)
    monkeypatch.setattr(switcher, '_refresh_inactive_auth', refresh)
    switcher.accounts_snapshot()
    assert len(calls) == 2 and calls[1]['refreshed']


def test_display_read_only_schema(usage, monkeypatch):
    switcher, home, baseline = usage
    write(home / 'auth.json', dict(baseline, rotation=1))
    before = switcher._credential_path('1').read_bytes()
    monkeypatch.setattr(cs, 'slot_custody', lambda *_: cs.Custody('quiescent', 'no_writers'))
    row = switcher.list_payload()['accounts'][0]
    assert list(row) == ['number', 'email', 'authMode', 'planType', 'label', 'active',
                         'disabled', 'session']
    assert row['session'] == {'state': 'idle', 'capturePending': True,
                              'runCount': 0, 'shared': False}
    assert switcher.status()['sessions'] == [{'number': 1, 'session': row['session']}]
    assert switcher._credential_path('1').read_bytes() == before


def test_copied_routing_never_receives_session_token(usage, monkeypatch):
    switcher, home, _ = usage
    (home / 'config.toml').write_text('chatgpt_base_url = "https://invalid.test"')
    monkeypatch.setattr(module, 'fetch_codex_usage', lambda *_args, **_kw: pytest.fail('fetch'))
    assert switcher.accounts_snapshot().accounts[0].usage.last_error


def test_live_divergence_is_unavailable(usage, monkeypatch):
    switcher, home, baseline = usage
    write(home / 'auth.json', dict(baseline, rotation=1))
    write(switcher._credential_path('1'), dict(baseline, rotation=2))
    monkeypatch.setattr(module, 'fetch_codex_usage', lambda *_args, **_kw: pytest.fail('fetch'))
    assert switcher.accounts_snapshot().accounts[0].usage.last_error


def test_default_remains_active_and_metadata_can_change_live(usage, monkeypatch):
    switcher, _, baseline = usage
    write(switcher.auth_file, baseline)
    monkeypatch.setattr(switcher, 'current_account_number', lambda: '1')
    switcher.set_account_disabled('1', True)
    row = switcher.list_payload()['accounts'][0]
    assert row['active'] and row['disabled']
    assert row['session']['state'] == 'live'
    assert row['session']['capturePending']


def test_usage_entry_resolves_instead_of_trusting_caller(usage, monkeypatch):
    switcher, home, baseline = usage
    rotated = dict(baseline, rotation=1)
    write(home / 'auth.json', rotated)
    seen = []
    monkeypatch.setattr(module, 'fetch_codex_usage', lambda auth, **_: seen.append(auth) or {})
    switcher._usage_entry('1', baseline, True, 100, is_active=False)
    assert seen == [rotated]


def test_missing_live_auth_is_unavailable(usage, monkeypatch):
    switcher, home, _ = usage
    (home / 'auth.json').unlink()
    monkeypatch.setattr(module, 'fetch_codex_usage', lambda *_args, **_kw: pytest.fail('fetch'))
    assert switcher.accounts_snapshot().accounts[0].usage.last_error
