"""Offline memory tests: no provider calls, audio devices, or live sockets."""
import threading
import time
import sys
from pathlib import Path
import tempfile
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from duplex.session_memory import SessionMemory
from duplex.session_store import SessionStore


class Store:
    def __init__(self):
        self.rows = []

    def start_session(self, session_id, metadata=None):
        pass

    def append_turn(self, session_id, role, text, **meta):
        row = dict(id=len(self.rows) + 1, session_id=session_id, role=role,
                   text=text, playback_verified=meta['playback_verified'], **{
                       k: v for k, v in meta.items() if k != 'playback_verified'})
        self.rows.append(row)
        return row

    def list_turns(self, session_id, limit=None, newest_first=False):
        rows = [r for r in self.rows if r['session_id'] == session_id]
        if newest_first:
            rows = rows[::-1]
        return rows[:limit] if limit is not None else rows

    def search_turns(self, query, session_id=None, limit=10):
        words = query.casefold().split()
        rows = [r for r in self.rows if r['session_id'] == session_id]
        rows.sort(key=lambda r: (sum(w in r['text'].casefold() for w in words), r['id']), reverse=True)
        return rows[:limit]


class Reply:
    def __init__(self, text):
        self.text = text
        self.errors = []


def wait_summary(memory, expected=1):
    until = time.monotonic() + 2
    while time.monotonic() < until:
        if memory.diagnostics()['summary_runs'] >= expected:
            return
        time.sleep(.005)
    raise AssertionError(memory.diagnostics())


def test_context_keeps_early_figures_and_raw_speech_after_long_reset():
    store = Store()
    memory = SessionMemory(store, 's', summary_client_factory=lambda: None, summary_every=100)
    memory.observe('user', 'I want to learn this ticket. Ask questions and check my reasoning.')
    memory.observe('user', 'Um, there were 244 attempts, three removed, and 241 failures; restore said 95.')
    memory.observe('user', 'Correction, it was 244, not 231. The casing share is unknown.')
    memory.observe('assistant', 'Invented diagnosis about 95 parents.', playback_verified=False)
    for index in range(20):
        memory.observe('user', f'Intervening dialogue turn {index}.')
    context = memory.context('How many failed? 244 casing', recent_limit=3)
    assert '244 attempts' in context
    assert '241 failures' in context
    assert 'casing share is unknown' in context
    assert 'Ask questions and check my reasoning' in context
    if 'Invented diagnosis' in context:
        assert 'assistant generated, delivery unverified' in context
    memory.close()


def test_digest_requires_exact_user_span_and_keeps_disfluency_for_speech_feedback():
    store = Store()

    class Client:
        def ask(self, prompt, timeout):
            assert 'speech fillers or self-repairs' in prompt
            assert 'Um, I-I meant 244, sorry' in prompt
            return Reply('{"items":[{"kind":"correction","text":"User repaired the number to 244",'
                         '"source_id":1,"quote":"I-I meant 244"},'
                         '{"kind":"known","text":"Fabricated",'
                         '"source_id":999,"quote":"Fabricated"}]}')

    memory = SessionMemory(store, 's', summary_client_factory=Client, summary_every=1,
                           mode='speech_feedback')
    memory.observe('user', 'Um, I-I meant 244, sorry.')
    wait_summary(memory)
    context = memory.context('number')
    assert 'correction (user quote)' in context
    assert 'I-I meant 244' in context
    assert 'Fabricated' not in context
    assert memory.version == (1, 1, 'speech_feedback')
    memory.close()


def test_slow_digest_never_blocks_new_turn_or_context_and_failure_uses_ledger():
    store = Store()
    entered = threading.Event()
    release = threading.Event()

    class SlowClient:
        def ask(self, prompt, timeout):
            entered.set()
            release.wait(1)
            raise RuntimeError('provider unavailable')

    memory = SessionMemory(store, 's', summary_client_factory=SlowClient, summary_every=1)
    memory.observe('user', 'The removal count is 244.')
    assert entered.wait(.5)
    start = time.monotonic()
    memory.observe('user', 'Actually, the causal share is still unknown.')
    context = memory.context('causal share')
    assert time.monotonic() - start < .2
    assert 'causal share is still unknown' in context
    release.set()
    until = time.monotonic() + 2
    while time.monotonic() < until and not memory.diagnostics()['summary_error']:
        time.sleep(.005)
    assert memory.diagnostics()['summary_error']
    assert 'removal count is 244' in memory.context('removal count')
    memory.close()


def test_resume_reconstructs_ledger_without_waiting_for_summary():
    store = Store()
    first = SessionMemory(store, 'same', summary_client_factory=lambda: None, summary_every=100)
    first.observe('user', 'My goal is to understand each status outcome.')
    first.close()
    resumed = SessionMemory(store, 'same', summary_client_factory=lambda: None, summary_every=100)
    assert resumed.version[0] == 1
    assert 'understand each status outcome' in resumed.context('goal')
    resumed.close()


def test_provisional_asr_is_not_evidence():
    store = Store()
    memory = SessionMemory(store, 's', summary_client_factory=lambda: None)
    assert memory.observe('user', 'wrong number 231', final=False) is None
    memory.observe('user', 'Correct number 244', final=True)
    assert 'wrong number 231' not in memory.context('number')
    memory.close()


def test_real_sqlite_store_persists_and_restores_digest():
    class Client:
        def ask(self, prompt, timeout):
            return Reply('{"items":[{"kind":"known","text":"244 attempts reported",'
                         '"source_id":1,"quote":"244 attempts"}]}')

    with tempfile.TemporaryDirectory() as temp:
        store = SessionStore(Path(temp) / 'session.sqlite3')
        memory = SessionMemory(store, 'resume-me', summary_client_factory=Client, summary_every=1)
        memory.observe('user', 'I reported 244 attempts.', event_id='event-1')
        wait_summary(memory)
        until = time.monotonic() + 2
        while time.monotonic() < until and not store.list_summaries('resume-me'):
            time.sleep(.005)
        assert store.list_summaries('resume-me')
        memory.close()
        store.close()
        reopened = SessionStore(Path(temp) / 'session.sqlite3')
        resumed = SessionMemory(reopened, 'resume-me', summary_client_factory=Client, summary_every=1)
        assert resumed.version == (1, 1, 'discussion')
        assert 'known (user quote)' in resumed.context('attempts')
        assert '244 attempts' in resumed.context('attempts')
        resumed.close()
        reopened.close()


def test_real_fts_expands_numeric_query_to_spoken_number():
    with tempfile.TemporaryDirectory() as temp:
        store = SessionStore(Path(temp) / 'session.sqlite3')
        memory = SessionMemory(store, 'number-words', summary_client_factory=lambda: None,
                               summary_every=100)
        for index in range(12):
            memory.observe('user', f'Ordinary topic {index}.')
        memory.observe('user', 'Two hundred forty four removal attempts were reported.')
        for index in range(12):
            memory.observe('user', f'Another topic {index}.')
        assert 'Two hundred forty four removal attempts' in memory.context('244', recent_limit=2)
        memory.close()
        store.close()


def test_tight_context_reserves_latest_and_respects_size():
    store = Store()
    memory = SessionMemory(store, 'tight', summary_client_factory=lambda: None, summary_every=1000)
    for index in range(16):
        memory.observe('user', f'Old context {index}: ' + 'x' * 500)
    memory.observe('user', 'LATEST CORRECTION: the cause is still unknown, and 241 failed.')
    context = memory.context('cause unknown', max_chars=1000, recent_limit=2)
    assert len(context) <= 1000
    assert 'LATEST CORRECTION' in context
    assert '241 failed' in context
    memory.close()


def test_long_summary_snapshot_keeps_early_goal_and_mode_switch():
    store = Store()
    prompts = []

    class Client:
        model = 'test-qwen'

        def ask(self, prompt, timeout):
            prompts.append(prompt)
            return Reply('{"items":[{"kind":"goal","text":"Ask questions to teach ticket",'
                         '"source_id":1,"quote":"Ask questions"}]}')

    memory = SessionMemory(store, 'long', summary_client_factory=Client, summary_every=1000)
    memory.observe('user', 'Ask questions to help me learn the ticket.')
    for index in range(55):
        memory.observe('user', f'Long turn {index}: ' + 'x' * 640)
    memory.set_mode('speech_feedback')
    wait_summary(memory)
    assert 'Ask questions to help me learn' in prompts[0]
    assert 'Long turn 54' in prompts[0]
    assert 'speech fillers or self-repairs' in prompts[0]
    metrics = memory.diagnostics()['last_summary']
    assert metrics['model'] == 'test-qwen'
    assert metrics['duration_s'] >= 0
    assert metrics['version'] == 56
    memory.set_mode('discussion')
    wait_summary(memory, expected=2)
    assert 'Compress incidental speech disfluency' in prompts[1]
    memory.close()


def test_close_prevents_late_summary_database_write():
    store = Store()
    started = threading.Event()
    release = threading.Event()

    class Client:
        def ask(self, prompt, timeout):
            started.set()
            release.wait(1)
            return Reply('{"items":[{"kind":"known","text":"A fact",'
                         '"source_id":1,"quote":"A fact"}]}')

    memory = SessionMemory(store, 'closing', summary_client_factory=Client, summary_every=1)
    memory.observe('user', 'A fact exists.')
    assert started.wait(.5)
    assert memory.close(timeout=.01) is False
    release.set()
    assert memory.close(timeout=2) is True
    assert memory.diagnostics()['summary_runs'] == 0


def test_persistence_failure_is_visible_while_digest_remains_usable():
    class FailingStore(Store):
        def save_summary(self, session_id, text, source_event_ids, version=None):
            raise OSError('disk write failed')

    class Client:
        def ask(self, prompt, timeout):
            return Reply('{"items":[{"kind":"known","text":"244 attempted",'
                         '"source_id":1,"quote":"244 attempted"}]}')

    store = FailingStore()
    memory = SessionMemory(store, 'failed-write', summary_client_factory=Client, summary_every=1)
    memory.observe('user', '244 attempted removals.')
    until = time.monotonic() + 2
    while time.monotonic() < until and memory.diagnostics()['persistence_error'] is None:
        time.sleep(.005)
    assert 'disk write failed' in memory.diagnostics()['persistence_error']
    assert '244 attempted' in memory.context('removals')
    memory.close()


def test_inflight_old_mode_digest_is_discarded_after_mode_switch():
    store = Store()
    entered = threading.Event()
    release = threading.Event()
    calls = []

    class Client:
        def ask(self, prompt, timeout):
            calls.append(prompt)
            if len(calls) == 1:
                entered.set()
                release.wait(1)
                note = 'Old discussion note'
            else:
                note = 'New speech feedback note'
            return Reply('{"items":[{"kind":"goal","text":"' + note + '",'
                         '"source_id":1,"quote":"speech feedback"}]}')

    memory = SessionMemory(store, 'mode-switch', summary_client_factory=Client, summary_every=1)
    memory.observe('user', 'Please give me speech feedback.')
    assert entered.wait(.5)
    memory.set_mode('speech_feedback')
    release.set()
    wait_summary(memory)
    context = memory.context('speech feedback')
    assert 'speech feedback' in context
    assert 'Old discussion note' not in context
    assert memory._summary[0]['text'] == 'New speech feedback note'
    assert memory.diagnostics()['last_summary']['mode'] == 'speech_feedback'
    memory.close()


def test_implicit_followup_retrieves_older_topic_evidence():
    with tempfile.TemporaryDirectory() as temp:
        store = SessionStore(Path(temp) / 'session.sqlite3')
        memory = SessionMemory(store, 'implicit', summary_client_factory=lambda: None,
                               summary_every=1000)
        for index in range(4):
            memory.observe('user', f'Initial chassis note {index}.')
        memory.observe('user', 'I have never run the battery endurance test.')
        for index in range(16):
            memory.observe('user', f'Discussing the bracket geometry, part {index}.')
        memory.observe('user', 'Back to the battery module.')
        memory.observe('user', 'Did we already try it?')
        context = memory.context('Did we already try it?', recent_limit=2, max_chars=1800)
        assert 'never run the battery endurance test' in context
        memory.close()
        store.close()


def test_digest_rejects_claim_that_reverses_uncertain_or_negative_quote():
    lines = ['[U1] I have never run the battery endurance test.',
             '[U2] I think the controller may be overheating, but I have not measured it.']
    invalid = ('{"items":['
               '{"kind":"known","text":"The battery endurance test failed",'
               '"source_id":1,"quote":"never run the battery endurance test"},'
               '{"kind":"known","text":"The controller overheated",'
               '"source_id":2,"quote":"controller may be overheating"}]}')
    try:
        SessionMemory._validate(invalid, lines)
    except ValueError:
        pass
    else:
        raise AssertionError('Unsupported digest was accepted')


def test_digest_rejects_contracted_negation_as_a_passing_test():
    lines = ["[U1] I haven't run the battery endurance test."]
    invalid = ('{"items":[{"kind":"known","text":"The battery endurance test passed",'
               '"source_id":1,"quote":"haven\'t run the battery endurance test"}]}')
    with pytest.raises(ValueError, match='No source-verified notes'):
        SessionMemory._validate(invalid, lines)


def test_prior_digest_interpretation_is_not_replayed_to_summarizer():
    memory = SessionMemory(Store(), 'prior', summary_client_factory=lambda: None,
                           summary_every=1000)
    prompt = memory._prompt(['[U1] I suspect a loose wire.'],
                            [{'kind': 'known', 'text': 'The wire was confirmed loose',
                              'source_id': 1, 'quote': 'I suspect a loose wire'}], 'discussion')
    assert 'The wire was confirmed loose' not in prompt
    assert 'I suspect a loose wire' in prompt
    memory.close()


def test_digest_quotes_are_rendered_without_model_interpretation():
    store = Store()
    memory = SessionMemory(store, 'quotes', summary_client_factory=lambda: None,
                           summary_every=1000)
    memory.observe('user', 'I suspect a loose wire, but have not inspected it.')
    memory._summary = [{'kind': 'hypothesis', 'text': 'The wire was confirmed loose',
                        'source_id': 1, 'quote': 'I suspect a loose wire'}]
    context = memory.context('wire')
    assert 'hypothesis (user quote)' in context
    assert 'I suspect a loose wire' in context
    assert 'confirmed loose' not in context
    memory.close()
