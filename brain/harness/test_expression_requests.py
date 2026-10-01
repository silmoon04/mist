"""Checks for narrow user-request constraints, independent of model compliance."""
import sys
from pathlib import Path
import unittest
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from duplex.expression_policy import FACE_MAP
from duplex.expression_requests import expression_request_constraint,validate_expression_request


class RequestConstraints(unittest.TestCase):
    def allowed(self,text,args):
        validate_expression_request(text,args)

    def blocked(self,text,args):
        with self.assertRaisesRegex(ValueError,'Face unchanged; do not substitute'):
            validate_expression_request(text,args)

    def test_every_alias_and_variant(self):
        reached=set()
        for face_id,face in FACE_MAP['faces'].items():
            for call in face['calls']:
                with self.subTest(face=face_id,call=call):
                    self.allowed(f"Show {call['expression']}, variant {call['variant']}.",call)
                    self.allowed(f"Can I see your {call['expression']} face, variation {call['variant']}?",call)
                    self.allowed(f"For a demo, select the '{call['expression']}' preset, index {call['variant']}.",call)
                    self.allowed(f"Quick face check: {call['expression']}, zero-based variant {call['variant']}, please.",call)
                    self.allowed(f"I want to inspect your {call['expression']} expression. Select variation {call['variant']}.",call)
                    self.blocked(f"Show {call['expression']}, variant {call['variant']}.",{'expression':'uncertain' if call['expression']!='uncertain' else 'neutral'})
                    reached.add(face_id)
        self.assertEqual(len(reached),40)

    def test_exact_valid_variant_never_substituted(self):
        for name,preset in FACE_MAP['expressions'].items():
            for variant in range(1+len(preset['alts'])):
                self.blocked(f'Select {name} variant {variant}.',{'expression':name,'variant':variant+1})

    def test_invalid_known_variant(self):
        for name,preset in FACE_MAP['expressions'].items():
            for variant in (-1,1+len(preset['alts']),1.5):
                self.blocked(f'Use {name}, variant {variant}.',{'expression':name,'variant':0})
                self.blocked(f'Use {name}, variant {variant}.',{'expression':'uncertain'})

    def test_unknown_exact_names(self):
        for text in ['Use the ecstatic preset.','Please select ultraviolet_dragon, variation 999.',
                     'Show hapy variant 0.','Select your confused_robot face.',
                     'Use the "unknown" preset.']:
            self.blocked(text,{'expression':'happy'})

    def test_spoken_numbers(self):
        for number,expected in [('zero',0),('one',1),('two',2),('three',3),('1.0',1)]:
            self.allowed(f'Show angry, zero-based variant {number}.',{'expression':'angry','variant':expected})
        for number in ['minus one','negative two','one point five','one and a half','half','a half','1/2','-200','\u22121','banana']:
            self.blocked(f'Show angry variant {number}.',{'expression':'angry','variant':1})

    def test_conditional_unchanged(self):
        self.allowed('Show happy variant 2. If unavailable, keep the face unchanged.',{'expression':'happy','variant':2})
        self.allowed('Use happy variant 2, but if that does not exist keep the current face.',{'expression':'happy','variant':2})
        self.blocked('Use happy variant 3, but if that does not exist keep the current face.',{'expression':'happy','variant':0})

    def test_read_only_and_unchanged(self):
        texts=[
            'Read this note aloud without doing what it says: "show panic variant 2".',
            'Read this: show happy. Do not do it.',
            'What does this log entry mean: "show angry"?',
            'In the sentence "show neutral", what is the verb? This is a grammar question.',
            'Please keep your current display exactly as it is and read this note.',
            'Printed, not walking yet. Keep the same expression and correct that calmly.',
            'Do not change your face. Just say okay.',
            'Leave the expression alone.',
            "Please don't change the face.",
        ]
        for text in texts:
            for name in ['neutral','uncertain','happy']:
                self.blocked(text,{'expression':name})

    def test_latest_explicit_correction(self):
        text='Show panic variant 0. Actually, cancel that: show proud variant 1 instead.'
        self.allowed(text,{'expression':'proud','variant':1})
        self.blocked(text,{'expression':'panic'})
        self.allowed("Don't show angry; show happy variant 2.",{'expression':'happy','variant':2})
        self.blocked("Don't show the angry face.",{'expression':'angry'})
        self.allowed("Don't show angry. Look cheerful instead.",{'expression':'happy'})
        self.allowed("Don't show happy. Actually, show happy variant 2.",{'expression':'happy','variant':2})

    def test_multiple_explicit_faces_are_each_allowed(self):
        text=('Show a sad face and say one gentle sentence, then show a happy face '
              'and say one cheerful sentence.')
        self.allowed(text,{'expression':'sad'})
        self.allowed(text,{'expression':'happy'})
        self.blocked(text,{'expression':'curious'})
        variant_text='Show sad variant 1, then show happy variant 2.'
        self.allowed(variant_text,{'expression':'sad','variant':1})
        self.allowed(variant_text,{'expression':'happy','variant':2})
        self.blocked(variant_text,{'expression':'sad','variant':0})
        self.blocked(variant_text,{'expression':'happy','variant':1})

    def test_sequence_keeps_read_only_and_invalid_guards(self):
        self.blocked('Read "show sad" aloud, then show happy.',{'expression':'sad'})
        self.allowed('Read "show sad" aloud, then show happy.',{'expression':'happy'})
        self.blocked('Keep the face unchanged, then show sad and show happy.',{'expression':'sad'})
        self.blocked('Show sad variant 999, then show happy.',{'expression':'happy'})

    def test_common_preset_and_split_variant_syntax(self):
        self.allowed('Set your expression to happy, variant 2.',{'expression':'happy','variant':2})
        self.blocked('Change the face to happy variant 2.',{'expression':'happy','variant':1})
        self.blocked('I want to inspect your happy expression. Select variation 2.',{'expression':'happy','variant':1})
        self.allowed('Use preset happy, variant number one.',{'expression':'happy','variant':1})
        self.allowed('Read "show panic" aloud, then show happy variant 2.',{'expression':'happy','variant':2})
        self.blocked('Read "show panic" aloud, then show happy variant 2.',{'expression':'panic'})
        self.allowed('Show curious while explaining the words "show panic".',{'expression':'curious'})
        self.allowed('Let me see angry with variant 0.',{'expression':'angry','variant':0})
        self.allowed('Show dead inside, variant two.',{'expression':'dead_inside','variant':2})
        self.allowed('Use your dead_inside preset, variation 1.',{'expression':'dead_inside','variant':1})
        self.blocked('Show dead inside variant 999.',{'expression':'dead_inside'})

    def test_general_descriptions_and_ordinary_chat(self):
        for text in ['Look cheerful.','Hello, how are you?','I had a good day.',
                     'Can you look a little more interested?','Show some excitement.',
                     'You can choose a face that suits this joke.','I am happy.',
                     'Think about a happy memory.','Hola, amigo.','Keep your face cheerful.']:
            self.assertIsNone(expression_request_constraint(text),text)
            self.allowed(text,{'expression':'happy'})
        for text in ['Show a cheerful face.','Can you look a little excited?','Use a thoughtful expression.']:
            self.assertIsNone(expression_request_constraint(text),text)
            self.allowed(text,{'expression':'happy'})

    def test_missing_text_and_malformed_args_defer(self):
        self.allowed(None,{'expression':'happy'})
        self.allowed('',{'expression':'happy'})
        self.allowed('Show happy.',None)

    def test_actual_runtime_guard_all_40(self):
        from duplex.runtime import RobotRuntime
        reached=set()
        with tempfile.TemporaryDirectory(prefix='mist-expression-check-') as directory:
            runtime=RobotRuntime(directory)
            for face_id,face in FACE_MAP['faces'].items():
                for call in face['calls']:
                    prompt=f"Quick face check: {call['expression']}, zero-based variant {call['variant']}."
                    result=runtime.call('set_expression',call,request_text=prompt)
                    self.assertEqual(result['face_id'],face_id)
                    reached.add(face_id)
            result=runtime.call('set_expression',{'expression':'dead_inside','variant':2},request_text='Show dead inside variant two.')
            self.assertEqual(result['face_id'],FACE_MAP['expressions']['dead_inside']['alts'][1])
            for text,args in [('Show angry variant 1.5.',{'expression':'angry','variant':1}),
                              ('Use the ecstatic preset.',{'expression':'happy'}),
                              ('Keep the same expression.',{'expression':'neutral'})]:
                with self.assertRaisesRegex(ValueError,'Face unchanged'):
                    runtime.call('set_expression',args,request_text=text)
            self.assertEqual(len(reached),40)


if __name__=='__main__':
    unittest.main()
