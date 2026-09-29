"""Preference persistence must not turn temporary conversation into memory."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from duplex.memory_requests import validate_memory_request
from duplex.runtime import RobotRuntime
from duplex.session_store import SessionStore


class MemoryRequestTests(unittest.TestCase):
    def test_explicit_requests(self):
        for text in ('Remember this preference exactly: No jokes when I am tired.',
                     'Please save that I prefer short replies.',
                     'And remember: I prefer short answers unless I ask for detail.',
                     'Could you remember that I like gentle greetings?',
                     'From now on, no loud greetings.',
                     'Save this for future conversations: short replies.',
                     'Remember this: "No jokes when I am tired."'):
            with self.subTest(text=text):
                validate_memory_request(text)

    def test_temporary_or_incidental_context(self):
        for text in ('Short replies today.', 'Stop joking for now.',
                     'My friend Maya is drawing your faces.', 'Keep it gentle.',
                     'Do you remember what I said?', 'Remember this for now.',
                     'Do not save this preference.',
                     'Read this note: "Remember that I like jokes."',
                     'Translate the command: remember that I like jokes.'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_memory_request(text)

    def test_runtime_rejection_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = RobotRuntime(directory)
            with self.assertRaises(ValueError):
                runtime.call('remember', {'note':'Prefers short replies'}, request_text='Short replies today.')
            self.assertEqual(runtime.memory, [])
            self.assertFalse(runtime.memory_path.exists())
            note = 'No jokes when I am tired.'
            runtime.call('remember', {'note':note}, request_text='Remember this preference exactly: ' + note)
            self.assertEqual(runtime.memory[0]['text'], note)

    def test_cross_session_recall_understands_a_natural_question(self):
        with tempfile.TemporaryDirectory() as directory:
            database = SessionStore(Path(directory) / 'sessions.sqlite3')
            try:
                database.append_turn('earlier', 'user',
                                     'The ticket had 244 removal attempts and the cause is unknown.')
                database.append_turn('current', 'user', 'We are discussing another topic.')
                runtime = RobotRuntime(Path(directory) / 'new-conversation')
                runtime.session_database = database
                runtime.session_id = 'current'
                result = runtime.call('recall',
                                      {'scope':'all', 'query':'What did I say about the ticket?'})
                self.assertEqual(len(result['past_session_matches']), 1)
                self.assertEqual(result['past_session_matches'][0]['session_id'], 'earlier')
                self.assertIn('244 removal attempts', result['past_session_matches'][0]['text'])
                self.assertEqual(runtime.call('recall',
                    {'scope':'all', 'query':'What did I say?'} )['past_session_matches'], [])
            finally:
                database.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
