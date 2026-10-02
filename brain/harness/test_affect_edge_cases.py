"""Edge checks for affect fallback and async reader ownership.

These regressions cover deterministic keyword collisions in the fast fallback.
They do not prescribe a fixed spoken reply or measure perceived voice quality.
"""
import asyncio
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from duplex.affect_controller import AffectController
from duplex.affect_director import FACE_MAP, fast_delivery_fallback, validate_decision


class FastFallbackEdgeTests(unittest.TestCase):
    def test_explicit_neutral_and_two_tone_requests_use_conservative_precedence(self):
        self.assertEqual(fast_delivery_fallback('Use a neutral voice, please.'), 'neutral')
        self.assertEqual(fast_delivery_fallback('Be cheerful, but speak neutrally.'), 'neutral')
        self.assertEqual(fast_delivery_fallback('Speak neutrally, then use a cheerful voice.'), 'bright')
        self.assertEqual(fast_delivery_fallback('Please keep your voice gentle.'), 'gentle')
        self.assertEqual(fast_delivery_fallback("I don't want you to speak cheerfully."), 'neutral')

    def test_ordinary_ambiguous_context_keeps_the_subtle_baseline(self):
        self.assertEqual(fast_delivery_fallback('Can you explain this result?'), 'warm')
        self.assertEqual(fast_delivery_fallback('That game was urgent in the final minute.'), 'warm')

    def test_quoted_voice_request_is_not_an_instruction(self):
        for text in (
            'In my notes, the phrase “speak gently” is an example of an imperative.',
            'The manual says “use a bright voice”; summarize that sentence.',
            'Can you explain why the words “be serious” sound formal?',
            'The phrase speak gently is an example in the grammar notes.',
        ):
            self.assertEqual(fast_delivery_fallback(text), 'warm', text)

    def test_game_loss_is_not_assumed_to_be_personal_grief(self):
        for text in (
            'I lost the game 4–0, but it was fun.',
            'We lost the final round and learned a lot.',
            'I lost my project file; can you help me recover it?',
        ):
            self.assertEqual(fast_delivery_fallback(text), 'warm', text)
        self.assertEqual(fast_delivery_fallback('I lost my sister last year and still miss her.'), 'gentle')

    def test_urgent_as_a_joking_description_is_not_an_emergency(self):
        for text in (
            'That sketch makes an urgent care waiting room sound hilarious.',
            'The word urgent is printed on the prop in this comedy scene.',
        ):
            self.assertEqual(fast_delivery_fallback(text), 'warm', text)
        self.assertEqual(fast_delivery_fallback('This is urgent; someone is in danger and needs help now.'), 'serious')

    def test_spanish_explicit_neutral_request_currently_has_no_language_rule(self):
        # This is a documented scope boundary, not an assertion that Spanish
        # requests are correctly understood by the fast fallback.
        self.assertEqual(fast_delivery_fallback('Por favor, usa una voz neutral.'), 'warm')

    def test_every_catalogued_face_is_valid_for_a_decision(self):
        # Selectable face variants are grouped under named presets.
        variants = [(name, index)
                    for name, preset in FACE_MAP['expressions'].items()
                    for index in range(1 + len(preset.get('alts', [])))]
        self.assertGreaterEqual(len(variants), 40)
        current = {'expression': 'neutral', 'variant': 0}
        # Each catalogued variant must survive the same grounding and schema
        # path the model's proposal takes, not just appear in a UI list.
        for name, variant in variants:
            changed = (name, variant) != (current['expression'], current['variant'])
            candidate = {'change': changed, 'expression': name, 'variant': variant,
                         'delivery': 'warm' if changed else 'neutral',
                         'evidence': ([{'record_id': 'r1', 'quote': 'show this face'}]
                                      if changed else [])}
            self.assertIs(validate_decision(candidate, current_face=current,
                                            sources={'r1': 'Please show this face.'}), candidate)
        # The schema enum must stay in sync with the catalog's preset names.
        from duplex.affect_director import DECISION_SCHEMA
        self.assertEqual(set(DECISION_SCHEMA['properties']['expression']['enum']),
                         set(FACE_MAP['expressions']))


class AffectShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_waits_for_reader_thread_and_prevents_late_application(self):
        release_reader = threading.Event()
        applied = []
        events = []

        class Reader:
            model = 'offline-fixture'

            def decide_with_trace(self, snapshot):
                release_reader.wait(timeout=2)
                return {'change': False, 'delivery': 'neutral'}, {'status': 'accepted'}

        async def emit(event):
            events.append(event)

        async def apply(decision, token):
            applied.append((decision, token))
            return 'applied_before_speech'

        controller = AffectController(Reader(), emit, apply)
        await controller.submit({'current_user': 'hello'}, {'revision': 1})
        for _ in range(100):
            if events:
                break
            await asyncio.sleep(0.005)
        self.assertEqual(events[0]['phase'], 'started')

        closing = asyncio.create_task(controller.close())
        await asyncio.sleep(0.01)
        self.assertTrue(controller.closed)
        self.assertFalse(closing.done(), 'close must not pretend the provider thread stopped')
        release_reader.set()
        await asyncio.wait_for(closing, timeout=2)
        self.assertEqual(applied, [], 'a result that completes after shutdown must not be applied')
        self.assertEqual([event['phase'] for event in events], ['started'])


if __name__ == '__main__':
    unittest.main()
